"""
Settings API: credentials, kill switch, connection test, system status.
"""

import os
import re
import json
import logging
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import verify_jwt, get_client_ip, _get_setting, _set_setting, _audit
from db.database import get_db, get_version
from db import crypto

logger = logging.getLogger("glyphTrader.api.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


class CredentialsUpdate(BaseModel):
    tradier_token: str
    tradier_account: str
    tradier_environment: str  # "sandbox" or "production"
    disclaimer_accepted: bool = False


class KillSwitchRequest(BaseModel):
    enabled: bool
    password: str | None = None  # Required to enable
    disclaimer_accepted: bool = False


class ObserveOnlyRequest(BaseModel):
    enabled: bool
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.get("/")
def get_settings(user: str = Depends(verify_jwt)):
    with get_db() as conn:
        environment = _get_setting(conn, "tradier_environment") or "sandbox"
        trading_enabled = _get_setting(conn, "trading_enabled") == "true"
        observe_only = _get_setting(conn, "observe_only") == "true"
        encrypted_token = _get_setting(conn, "tradier_api_token")
        encrypted_account = _get_setting(conn, "tradier_account_number")

        token_last4 = ""
        if encrypted_token and crypto.is_unlocked():
            try:
                full = crypto.decrypt(encrypted_token)
                token_last4 = full[-4:] if len(full) >= 4 else full
            except Exception:
                pass

        account_display = ""
        if encrypted_account and crypto.is_unlocked():
            try:
                account_display = crypto.decrypt(encrypted_account)
            except Exception:
                pass

        from license import is_production_licensed
        license_valid = is_production_licensed()

    return {
        "tradier_environment": environment,
        "tradier_token_last4": token_last4,
        "tradier_account": account_display,
        "trading_enabled": trading_enabled,
        "observe_only": observe_only,
        "system_locked": not crypto.is_unlocked(),
        "version": get_version(),
        "license_valid": license_valid,
    }


@router.put("/credentials")
def update_credentials(req: CredentialsUpdate, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not req.disclaimer_accepted:
        raise HTTPException(status_code=400, detail="You must accept the disclaimer before saving credentials")

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked. Log in to unlock.")

    if req.tradier_environment not in ("sandbox", "production"):
        raise HTTPException(status_code=400, detail="Environment must be 'sandbox' or 'production'")

    if req.tradier_environment == "production":
        from license import is_production_licensed
        if not is_production_licensed():
            raise HTTPException(
                status_code=403,
                detail="Production environment requires a valid license key. Enter your key in the Production License section below."
            )

    if not re.match(r"^[A-Za-z0-9]{20,50}$", req.tradier_token):
        raise HTTPException(status_code=400, detail="Invalid token format")

    with get_db() as conn:
        encrypted_token = crypto.encrypt(req.tradier_token)
        encrypted_account = crypto.encrypt(req.tradier_account)
        _set_setting(conn, "tradier_api_token", encrypted_token, encrypted=1)
        _set_setting(conn, "tradier_account_number", encrypted_account, encrypted=1)
        _set_setting(conn, "tradier_environment", req.tradier_environment)

        # Update degraded-mode token
        jwt_secret = _get_setting(conn, "jwt_secret")
        if jwt_secret:
            degraded = crypto.encrypt_with_key(req.tradier_token, jwt_secret)
            degraded_account = crypto.encrypt_with_key(req.tradier_account, jwt_secret)
            _set_setting(conn, "degraded_token", degraded, encrypted=1)
            _set_setting(conn, "degraded_account", degraded_account, encrypted=1)

        _audit(conn, "token_change", ip)

    return {"message": "Credentials updated"}


@router.put("/kill-switch")
def toggle_kill_switch(req: KillSwitchRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    with get_db() as conn:
        if req.enabled and not req.disclaimer_accepted:
            raise HTTPException(status_code=400, detail="You must accept the disclaimer before enabling trading")

        if req.enabled and not req.password:
            raise HTTPException(status_code=400, detail="Password required to enable trading")

        if req.enabled:
            import bcrypt
            pw_hash = _get_setting(conn, "admin_password_hash")
            if not pw_hash or not bcrypt.checkpw(req.password.encode(), pw_hash.encode()):
                raise HTTPException(status_code=401, detail="Invalid password")

        _set_setting(conn, "trading_enabled", "true" if req.enabled else "false")
        _audit(conn, "kill_switch", ip, {"trading_enabled": req.enabled})

    return {"trading_enabled": req.enabled}


@router.put("/observe-only")
def toggle_observe_only(req: ObserveOnlyRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)
    import bcrypt as bcrypt_lib

    with get_db() as conn:
        pw_hash = _get_setting(conn, "admin_password_hash")
        if not pw_hash or not bcrypt_lib.checkpw(req.password.encode(), pw_hash.encode()):
            raise HTTPException(status_code=401, detail="Invalid password")

        _set_setting(conn, "observe_only", "true" if req.enabled else "false")
        _audit(conn, "observe_only_toggle", ip, {"observe_only": req.enabled})

    return {"observe_only": req.enabled}


@router.post("/test-connection")
def test_connection(user: str = Depends(verify_jwt)):
    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    with get_db() as conn:
        encrypted_token = _get_setting(conn, "tradier_api_token")
        encrypted_account = _get_setting(conn, "tradier_account_number")
        environment = _get_setting(conn, "tradier_environment") or "sandbox"

    if not encrypted_token:
        raise HTTPException(status_code=400, detail="No Tradier credentials configured")

    try:
        token = crypto.decrypt(encrypted_token)
        account = crypto.decrypt(encrypted_account) if encrypted_account else ""
        from tradier.client import TradierClient
        client = TradierClient(api_token=token, account_number=account, environment=environment)
        balances = client.get_balances()
        return {
            "connected": True,
            "account_type": balances.get("account_type", "unknown"),
            "total_equity": balances.get("total_equity", 0),
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


@router.get("/system-status")
def system_status(user: str = Depends(verify_jwt)):
    from scheduler.jobs import get_scheduler_status
    status = get_scheduler_status()
    return {
        "version": get_version(),
        "locked": not crypto.is_unlocked(),
        "scheduler": status,
    }


@router.get("/check-updates")
def check_updates(user: str = Depends(verify_jwt)):
    import httpx
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"update_available": False, "error": "No GITHUB_TOKEN configured"}
    try:
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3.raw"}
        resp = httpx.get(
            f"https://api.github.com/repos/{os.environ.get('GITHUB_REPO', 'xochal/glyphTrader')}/contents/VERSION",
            headers=headers, timeout=10,
        )
        if resp.status_code != 200:
            return {"update_available": False, "error": "Could not fetch remote version"}
        remote_version = resp.text.strip()
        local_version = get_version()
        return {"update_available": remote_version != local_version, "remote_version": remote_version}
    except Exception:
        return {"update_available": False, "error": "Could not check for updates"}


@router.put("/password")
def change_password(req: PasswordChangeRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)
    import bcrypt as bcrypt_lib

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    with get_db() as conn:
        pw_hash = _get_setting(conn, "admin_password_hash")
        if not pw_hash or not bcrypt_lib.checkpw(req.current_password.encode(), pw_hash.encode()):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

        # BEGIN IMMEDIATE for atomicity
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Decrypt all encrypted values with old key
            encrypted_rows = conn.execute(
                "SELECT key, value FROM settings WHERE encrypted = 1"
            ).fetchall()
            decrypted = {}
            for r in encrypted_rows:
                try:
                    decrypted[r["key"]] = crypto.decrypt(r["value"])
                except Exception:
                    pass

            # Derive new key
            new_salt = crypto.generate_salt()
            crypto.unlock(req.new_password, new_salt)

            # Re-encrypt all values with new key
            for key, plaintext in decrypted.items():
                new_encrypted = crypto.encrypt(plaintext)
                conn.execute(
                    "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                    (new_encrypted, datetime.now(timezone.utc).isoformat(), key),
                )

            # Update password hash and salt
            new_hash = bcrypt_lib.hashpw(req.new_password.encode(), bcrypt_lib.gensalt()).decode()
            _set_setting(conn, "admin_password_hash", new_hash)
            _set_setting(conn, "fernet_salt", new_salt.hex())

            # Update degraded token
            jwt_secret = _get_setting(conn, "jwt_secret")
            if jwt_secret and "tradier_api_token" in decrypted:
                degraded = crypto.encrypt_with_key(decrypted["tradier_api_token"], jwt_secret)
                _set_setting(conn, "degraded_token", degraded, encrypted=1)

            _audit(conn, "password_change", ip, {"method": "settings"})
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return {"message": "Password changed successfully"}


class LicenseKeyRequest(BaseModel):
    key: str


@router.get("/license")
def get_license_status(user: str = Depends(verify_jwt)):
    from license import is_production_licensed
    with get_db() as conn:
        key_row = conn.execute("SELECT value FROM settings WHERE key = 'license_key'").fetchone()
        ver_row = conn.execute("SELECT value FROM settings WHERE key = 'license_version'").fetchone()
    has_key = key_row is not None
    stored_version = ver_row["value"] if ver_row else None
    current_version = get_version()
    return {
        "licensed": is_production_licensed(),
        "has_key": has_key,
        "version_match": stored_version == current_version if stored_version else False,
        "stored_version": stored_version,
        "current_version": current_version,
    }


@router.put("/license")
def activate_license(req: LicenseKeyRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)
    from license import store_license_key
    if not store_license_key(req.key):
        raise HTTPException(status_code=400, detail="Invalid license key for this version")
    with get_db() as conn:
        _audit(conn, "license_activate", ip, {"version": get_version()})
    return {"message": "Production license activated", "version": get_version()}


@router.delete("/license")
def deactivate_license(request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)
    from license import clear_license
    clear_license()
    # Force environment to sandbox
    with get_db() as conn:
        _set_setting(conn, "tradier_environment", "sandbox")
        _audit(conn, "license_deactivate", ip)
    return {"message": "License removed, environment set to sandbox"}


@router.get("/audit-log")
def get_audit_log(user: str = Depends(verify_jwt), limit: int = 50):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"events": [dict(r) for r in rows]}
