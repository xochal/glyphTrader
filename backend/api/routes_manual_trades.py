"""
Manual trade management API: orphan discovery, adoption, stop/target management,
hold mode, release, flatten all.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, field_validator

from api.auth import verify_jwt, get_client_ip, _audit, _get_setting
from db.database import get_db
from db import crypto

logger = logging.getLogger("glyphTrader.api.manual_trades")
router = APIRouter(prefix="/api/manual-trades", tags=["manual-trades"])


def _check_observe_only(conn):
    if _get_setting(conn, "observe_only") == "true":
        raise HTTPException(status_code=403, detail="Observe-only mode is active")


# --- Pydantic Models ---

class AdoptRequest(BaseModel):
    symbol: str
    shares: int
    entry_price_cents: int
    stop_mode: str  # 'atr', 'dollar', 'percent'
    stop_value: float
    ratchet_enabled: bool = False
    ratchet_mode: Optional[str] = None
    ratchet_value: Optional[float] = None
    targets_enabled: bool = True
    t1_mode: str = "atr"
    t1_value: float = 10.0
    t2_mode: str = "atr"
    t2_value: float = 15.0
    t3_mode: str = "atr"
    t3_value: float = 20.0
    t1_exit_pct: int = 70
    t2_exit_pct: int = 20
    t3_exit_pct: int = 10

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v):
        import re
        v = v.strip().upper()
        if not re.match(r'^[A-Z0-9.\-]{1,10}$', v):
            raise ValueError("Symbol must be 1-10 characters (letters, digits, dots, hyphens)")
        return v

    @field_validator("shares")
    @classmethod
    def validate_shares(cls, v):
        if v < 1 or v > 100000:
            raise ValueError("Shares must be between 1 and 100,000")
        return v

    @field_validator("entry_price_cents")
    @classmethod
    def validate_entry_price(cls, v):
        if v < 1 or v > 100000000:  # $1M max
            raise ValueError("Entry price out of range")
        return v

    @field_validator("stop_mode", "t1_mode", "t2_mode", "t3_mode")
    @classmethod
    def validate_mode(cls, v):
        if v not in ("atr", "dollar", "percent"):
            raise ValueError("Mode must be 'atr', 'dollar', or 'percent'")
        return v

    @field_validator("stop_value", "t1_value", "t2_value", "t3_value")
    @classmethod
    def validate_value(cls, v):
        if v <= 0 or v > 10000:
            raise ValueError("Value must be between 0 and 10,000")
        return v


class UpdateStopsRequest(BaseModel):
    stop_mode: str
    stop_value: float
    ratchet_enabled: bool = False
    ratchet_mode: Optional[str] = None
    ratchet_value: Optional[float] = None

    @field_validator("stop_mode")
    @classmethod
    def validate_mode(cls, v):
        if v not in ("atr", "dollar", "percent"):
            raise ValueError("Mode must be 'atr', 'dollar', or 'percent'")
        return v

    @field_validator("stop_value")
    @classmethod
    def validate_value(cls, v):
        if v <= 0 or v > 10000:
            raise ValueError("Value must be between 0 and 10,000")
        return v


class UpdateTargetsRequest(BaseModel):
    t1_mode: str
    t1_value: float
    t2_mode: str
    t2_value: float
    t3_mode: str
    t3_value: float
    t1_exit_pct: int
    t2_exit_pct: int
    t3_exit_pct: int

    @field_validator("t1_mode", "t2_mode", "t3_mode")
    @classmethod
    def validate_mode(cls, v):
        if v not in ("atr", "dollar", "percent"):
            raise ValueError("Mode must be 'atr', 'dollar', or 'percent'")
        return v

    @field_validator("t1_value", "t2_value", "t3_value")
    @classmethod
    def validate_value(cls, v):
        if v <= 0 or v > 10000:
            raise ValueError("Value must be between 0 and 10,000")
        return v

    @field_validator("t1_exit_pct", "t2_exit_pct", "t3_exit_pct")
    @classmethod
    def validate_pct(cls, v):
        if v < 0 or v > 100:
            raise ValueError("Exit percentage must be between 0 and 100")
        return v


class HoldModeRequest(BaseModel):
    enabled: bool


class FlattenRequest(BaseModel):
    password: str


# --- Rate Limiting Helpers ---

def _check_adoption_rate_limit(conn, ip: str):
    """Max 3 adoptions per 5 minutes."""
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM audit_log "
        "WHERE event_type = 'adopt_position' AND created_at > datetime('now', '-5 minutes')"
    ).fetchone()["cnt"]
    if count >= 3:
        raise HTTPException(status_code=429, detail="Adoption rate limit: max 3 per 5 minutes")


def _check_trade_rate_limit(conn, trade_id: int):
    """Max 1 update per 30 seconds per trade."""
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM audit_log "
        "WHERE event_type LIKE 'manual_update_%' AND details LIKE ? "
        "AND created_at > datetime('now', '-30 seconds')",
        (f'%"trade_id": {trade_id}%',),
    ).fetchone()["cnt"]
    if count >= 1:
        raise HTTPException(status_code=429, detail="Update rate limit: max 1 per 30 seconds per trade")


def _get_manual_trade(conn, trade_id: int):
    """Fetch a trade and verify it's manual + open."""
    trade = conn.execute(
        "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND status = 'open'",
        (trade_id,),
    ).fetchone()
    if not trade:
        raise HTTPException(status_code=404, detail="Manual trade not found or not open")
    return trade


# --- Endpoints ---

@router.get("/orphans")
def get_orphans(user: str = Depends(verify_jwt)):
    from tradier.reconciliation import get_orphan_cache
    return {"orphans": get_orphan_cache()}


@router.post("/orphans/{symbol}/dismiss")
def dismiss_orphan_endpoint(symbol: str, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)
    symbol = symbol.strip().upper()
    from tradier.reconciliation import dismiss_orphan
    dismiss_orphan(symbol)
    with get_db() as conn:
        _audit(conn, "dismiss_orphan", ip, {"symbol": symbol})
    return {"message": f"Orphan {symbol} dismissed"}


@router.post("/adopt")
def adopt_position(req: AdoptRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked. Log in to unlock.")

    with get_db() as conn:
        _check_observe_only(conn)
        if _get_setting(conn, "trading_enabled") == "false":
            raise HTTPException(status_code=400, detail="Trading is disabled (kill switch). Enable trading first.")

        _check_adoption_rate_limit(conn, ip)

    # Validate exit percentages sum to 100
    if req.targets_enabled:
        if req.t1_exit_pct + req.t2_exit_pct + req.t3_exit_pct != 100:
            raise HTTPException(status_code=400, detail="Exit percentages must sum to 100")

    # Validate ratchet config
    if req.ratchet_enabled and (not req.ratchet_mode or not req.ratchet_value):
        raise HTTPException(status_code=400, detail="Ratchet mode and value required when ratchet is enabled")

    from tradier.manual_trades import adopt_position as do_adopt
    result = do_adopt(
        symbol=req.symbol,
        shares=req.shares,
        entry_price_cents=req.entry_price_cents,
        config={
            "stop_mode": req.stop_mode,
            "stop_value": req.stop_value,
            "ratchet_enabled": req.ratchet_enabled,
            "ratchet_mode": req.ratchet_mode,
            "ratchet_value": req.ratchet_value,
            "targets_enabled": req.targets_enabled,
            "t1_mode": req.t1_mode,
            "t1_value": req.t1_value,
            "t2_mode": req.t2_mode,
            "t2_value": req.t2_value,
            "t3_mode": req.t3_mode,
            "t3_value": req.t3_value,
            "t1_exit_pct": req.t1_exit_pct,
            "t2_exit_pct": req.t2_exit_pct,
            "t3_exit_pct": req.t3_exit_pct,
        },
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    with get_db() as conn:
        _audit(conn, "adopt_position", ip, {"symbol": req.symbol, "shares": req.shares, "trade_id": result.get("trade_id")})

    return result


@router.put("/{trade_id}/stops")
def update_stops(trade_id: int, req: UpdateStopsRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    if req.ratchet_enabled and (not req.ratchet_mode or not req.ratchet_value):
        raise HTTPException(status_code=400, detail="Ratchet mode and value required when ratchet is enabled")

    from tradier.manual_trades import update_trade_stops
    with get_db() as conn:
        _check_observe_only(conn)
        _check_trade_rate_limit(conn, trade_id)
        trade = _get_manual_trade(conn, trade_id)

    result = update_trade_stops(trade_id, req.dict())

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    with get_db() as conn:
        _audit(conn, "manual_update_stops", ip, {"trade_id": trade_id})
    return result


@router.put("/{trade_id}/targets")
def update_targets(trade_id: int, req: UpdateTargetsRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    if req.t1_exit_pct + req.t2_exit_pct + req.t3_exit_pct != 100:
        raise HTTPException(status_code=400, detail="Exit percentages must sum to 100")

    from tradier.manual_trades import update_trade_targets
    with get_db() as conn:
        _check_observe_only(conn)
        _check_trade_rate_limit(conn, trade_id)
        trade = _get_manual_trade(conn, trade_id)

    result = update_trade_targets(trade_id, req.dict())

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    with get_db() as conn:
        _audit(conn, "manual_update_targets", ip, {"trade_id": trade_id})
    return result


@router.put("/{trade_id}/hold-mode")
def toggle_hold_mode(trade_id: int, req: HoldModeRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    from tradier.manual_trades import set_hold_mode
    with get_db() as conn:
        _check_observe_only(conn)
        _check_trade_rate_limit(conn, trade_id)
        trade = _get_manual_trade(conn, trade_id)

    result = set_hold_mode(trade_id, req.enabled)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    with get_db() as conn:
        _audit(conn, "manual_update_hold_mode", ip, {"trade_id": trade_id, "enabled": req.enabled})
    return result


@router.put("/{trade_id}/close")
def close_position(trade_id: int, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    from tradier.manual_trades import close_manual_position
    with get_db() as conn:
        _check_observe_only(conn)
        trade = _get_manual_trade(conn, trade_id)

    result = close_manual_position(trade_id)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    with get_db() as conn:
        _audit(conn, "manual_close", ip, {"trade_id": trade_id, "symbol": trade["symbol"]})
    return result


@router.put("/{trade_id}/release")
def release_position(trade_id: int, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    from tradier.manual_trades import release_position as do_release
    with get_db() as conn:
        _check_observe_only(conn)
        trade = _get_manual_trade(conn, trade_id)

    result = do_release(trade_id)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    with get_db() as conn:
        _audit(conn, "manual_release", ip, {"trade_id": trade_id, "symbol": trade["symbol"]})
    return result


@router.delete("/{trade_id}")
def delete_adopting_trade(trade_id: int, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    with get_db() as conn:
        _check_observe_only(conn)
        trade = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND position_state = 'ADOPTING'",
            (trade_id,),
        ).fetchone()
        if not trade:
            raise HTTPException(status_code=404, detail="No stuck ADOPTING trade found with that ID")

    from tradier.manual_trades import delete_adopting_trade as do_delete
    result = do_delete(trade_id)

    with get_db() as conn:
        _audit(conn, "delete_adopting", ip, {"trade_id": trade_id, "symbol": trade["symbol"]})
    return result


@router.post("/{trade_id}/retry-orders")
def retry_orders(trade_id: int, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    with get_db() as conn:
        _check_observe_only(conn)
        trade = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND position_state = 'ADOPTING'",
            (trade_id,),
        ).fetchone()
        if not trade:
            raise HTTPException(status_code=404, detail="No stuck ADOPTING trade found with that ID")

    from tradier.manual_trades import retry_adoption_orders
    result = retry_adoption_orders(trade_id)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    with get_db() as conn:
        _audit(conn, "retry_adoption", ip, {"trade_id": trade_id})
    return result


@router.post("/flatten-all")
def flatten_all(req: FlattenRequest, request: Request, user: str = Depends(verify_jwt)):
    ip = get_client_ip(request)

    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked")

    with get_db() as conn:
        _check_observe_only(conn)

    from tradier.flatten import flatten_all_positions
    result = flatten_all_positions(password=req.password, ip=ip)

    if "error" in result:
        status = result.get("status_code", 400)
        raise HTTPException(status_code=status, detail=result["error"])

    return result
