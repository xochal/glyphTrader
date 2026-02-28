"""
JWT auth: login, refresh (with rotation + revocation), logout, setup, recovery.
Single admin user — no username field.
"""

import os
import re
import hashlib
import secrets
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import bcrypt
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from pydantic import BaseModel

from db.database import get_db
from db import crypto

logger = logging.getLogger("glyphTrader.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_HOURS = 24
SESSION_TIMEOUT_MINUTES = 10


class LoginRequest(BaseModel):
    password: str


class SetupRequest(BaseModel):
    setup_token: str
    password: str


class RecoverRequest(BaseModel):
    recovery_key: str
    new_password: str
    tradier_token: str


def _get_setting(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key: str, value: str, encrypted: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, encrypted, updated_at) VALUES (?, ?, ?, ?)",
        (key, value, encrypted, now),
    )


def _audit(conn, event_type: str, ip: str, details: dict = None):
    conn.execute(
        "INSERT INTO audit_log (event_type, ip_address, details, created_at) VALUES (?, ?, ?, ?)",
        (event_type, ip, json.dumps(details or {}), datetime.now(timezone.utc).isoformat()),
    )


def _create_access_token(jwt_secret: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": "admin", "exp": exp}, jwt_secret, algorithm=JWT_ALGORITHM)


def _create_refresh_token() -> str:
    return secrets.token_hex(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def verify_jwt(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = auth[7:]
    with get_db() as conn:
        jwt_secret = _get_setting(conn, "jwt_secret")
    if not jwt_secret:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, jwt_secret, algorithms=[JWT_ALGORITHM])
        # Update last activity
        with get_db() as conn:
            _set_setting(conn, "last_activity", datetime.now(timezone.utc).isoformat())
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/status")
def auth_status():
    with get_db() as conn:
        setup_complete = _get_setting(conn, "setup_complete")
        locked = not crypto.is_unlocked()
    return {"setup_complete": setup_complete == "true", "locked": locked}


@router.post("/setup")
def setup(req: SetupRequest, request: Request):
    ip = get_client_ip(request)
    with get_db() as conn:
        if _get_setting(conn, "setup_complete") == "true":
            raise HTTPException(status_code=403, detail="Setup already completed")

        stored_token = _get_setting(conn, "setup_token")
        if not stored_token or req.setup_token != stored_token:
            _audit(conn, "setup_failure", ip, {"reason": "invalid_token"})
            raise HTTPException(status_code=403, detail="Invalid setup token")

        if len(req.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        # Hash password
        pw_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
        _set_setting(conn, "admin_password_hash", pw_hash)

        # Generate and store Fernet salt
        salt = crypto.generate_salt()
        _set_setting(conn, "fernet_salt", salt.hex())

        # Generate JWT secret
        jwt_secret = secrets.token_hex(32)
        _set_setting(conn, "jwt_secret", jwt_secret)

        # Generate recovery key
        recovery_key = secrets.token_hex(32)
        recovery_hash = bcrypt.hashpw(recovery_key.encode(), bcrypt.gensalt()).decode()
        _set_setting(conn, "recovery_key_hash", recovery_hash)

        # Initialize settings
        _set_setting(conn, "trading_enabled", "false")
        _set_setting(conn, "observe_only", "false")
        _set_setting(conn, "tradier_environment", "sandbox")
        _set_setting(conn, "setup_complete", "true")
        _set_setting(conn, "last_activity", datetime.now(timezone.utc).isoformat())

        # Delete setup token (no longer needed)
        conn.execute("DELETE FROM settings WHERE key = 'setup_token'")

        # Unlock system
        crypto.unlock(req.password, salt)

        _audit(conn, "setup_complete", ip)

        # Generate tokens
        access_token = _create_access_token(jwt_secret)
        refresh = _create_refresh_token()
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS)).isoformat()
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, expires_at, created_at) VALUES (?, ?, ?)",
            (_hash_token(refresh), expires, now),
        )

    response = {
        "message": "Setup complete",
        "access_token": access_token,
        "recovery_key": recovery_key,
    }
    resp = Response(content=json.dumps(response), media_type="application/json")
    resp.set_cookie(
        "refresh_token", refresh, httponly=True, secure=True, samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_HOURS * 3600, path="/api/auth",
    )
    return resp


@router.post("/login")
def login(req: LoginRequest, request: Request):
    ip = get_client_ip(request)

    with get_db() as conn:
        # Rate limit check
        fifteen_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM login_attempts WHERE ip_address = ? AND attempted_at > ? AND success = 0",
            (ip, fifteen_min_ago),
        ).fetchone()
        if row["cnt"] >= 5:
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again in 15 minutes.")

        pw_hash = _get_setting(conn, "admin_password_hash")
        if not pw_hash or not bcrypt.checkpw(req.password.encode(), pw_hash.encode()):
            conn.execute(
                "INSERT INTO login_attempts (ip_address, success, attempted_at) VALUES (?, 0, ?)",
                (ip, datetime.now(timezone.utc).isoformat()),
            )
            _audit(conn, "login_failure", ip)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Success
        conn.execute(
            "INSERT INTO login_attempts (ip_address, success, attempted_at) VALUES (?, 1, ?)",
            (ip, datetime.now(timezone.utc).isoformat()),
        )

        # Unlock if locked
        if not crypto.is_unlocked():
            salt_hex = _get_setting(conn, "fernet_salt")
            if salt_hex:
                crypto.unlock(req.password, bytes.fromhex(salt_hex))

        jwt_secret = _get_setting(conn, "jwt_secret")
        access_token = _create_access_token(jwt_secret)
        refresh = _create_refresh_token()
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS)).isoformat()
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, expires_at, created_at) VALUES (?, ?, ?)",
            (_hash_token(refresh), expires, now),
        )
        _set_setting(conn, "last_activity", now)
        _audit(conn, "login_success", ip)

    resp = Response(
        content=json.dumps({"access_token": access_token}),
        media_type="application/json",
    )
    resp.set_cookie(
        "refresh_token", refresh, httponly=True, secure=True, samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_HOURS * 3600, path="/api/auth",
    )
    return resp


@router.post("/refresh")
def refresh_token(request: Request):
    refresh = request.cookies.get("refresh_token")
    if not refresh:
        raise HTTPException(status_code=401, detail="No refresh token")

    token_hash = _hash_token(refresh)

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        if row["is_revoked"]:
            # Possible theft — revoke all
            conn.execute("UPDATE refresh_tokens SET is_revoked = 1")
            logger.warning("Revoked refresh token reused — revoking ALL tokens")
            raise HTTPException(status_code=401, detail="Token revoked")

        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Refresh token expired")

        # Check server-side session timeout
        last_activity = _get_setting(conn, "last_activity")
        if last_activity:
            last = datetime.fromisoformat(last_activity)
            if datetime.now(timezone.utc) - last > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
                crypto.lock()
                raise HTTPException(status_code=401, detail="Session timed out")

        # Rotate: revoke old, issue new
        conn.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ?", (token_hash,))

        jwt_secret = _get_setting(conn, "jwt_secret")
        access_token = _create_access_token(jwt_secret)
        new_refresh = _create_refresh_token()
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS)).isoformat()
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, expires_at, created_at) VALUES (?, ?, ?)",
            (_hash_token(new_refresh), expires, now),
        )
        _set_setting(conn, "last_activity", now)

    resp = Response(
        content=json.dumps({"access_token": access_token}),
        media_type="application/json",
    )
    resp.set_cookie(
        "refresh_token", new_refresh, httponly=True, secure=True, samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_HOURS * 3600, path="/api/auth",
    )
    return resp


@router.post("/logout")
def logout(request: Request):
    refresh = request.cookies.get("refresh_token")
    if refresh:
        token_hash = _hash_token(refresh)
        with get_db() as conn:
            conn.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ?", (token_hash,))
    resp = Response(content=json.dumps({"message": "Logged out"}), media_type="application/json")
    resp.delete_cookie("refresh_token", path="/api/auth")
    return resp


@router.post("/revoke-all")
def revoke_all(request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)
    with get_db() as conn:
        conn.execute("UPDATE refresh_tokens SET is_revoked = 1")
        _audit(conn, "session_revoke", ip, {"reason": "user_initiated"})
    return {"message": "All sessions revoked"}


@router.post("/recover")
def recover(req: RecoverRequest, request: Request):
    ip = get_client_ip(request)
    with get_db() as conn:
        # Rate limit: 3 / hour
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_log WHERE event_type = 'recovery_attempt' AND created_at > ?",
            (one_hour_ago,),
        ).fetchone()
        if row["cnt"] >= 3:
            raise HTTPException(status_code=429, detail="Too many recovery attempts")
        _audit(conn, "recovery_attempt", ip)

        stored_hash = _get_setting(conn, "recovery_key_hash")
        if not stored_hash or not bcrypt.checkpw(req.recovery_key.encode(), stored_hash.encode()):
            raise HTTPException(status_code=403, detail="Invalid recovery key")

        if len(req.new_password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        if not re.match(r"^[A-Za-z0-9]{20,50}$", req.tradier_token):
            raise HTTPException(status_code=400, detail="Invalid Tradier token format")

        # Reset password and re-encrypt
        pw_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
        new_salt = crypto.generate_salt()
        _set_setting(conn, "admin_password_hash", pw_hash)
        _set_setting(conn, "fernet_salt", new_salt.hex())

        crypto.unlock(req.new_password, new_salt)

        encrypted_token = crypto.encrypt(req.tradier_token)
        _set_setting(conn, "tradier_api_token", encrypted_token, encrypted=1)

        # Update degraded token
        jwt_secret = _get_setting(conn, "jwt_secret")
        if jwt_secret:
            degraded = crypto.encrypt_with_key(req.tradier_token, jwt_secret)
            _set_setting(conn, "degraded_token", degraded, encrypted=1)

        # New recovery key
        new_recovery = secrets.token_hex(32)
        recovery_hash = bcrypt.hashpw(new_recovery.encode(), bcrypt.gensalt()).decode()
        _set_setting(conn, "recovery_key_hash", recovery_hash)

        conn.execute("UPDATE refresh_tokens SET is_revoked = 1")
        _audit(conn, "password_change", ip, {"method": "recovery"})

    return {"message": "Password reset. Save your new recovery key.", "recovery_key": new_recovery}
