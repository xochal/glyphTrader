"""
Trade Settings API: CRUD for trading config overrides + disclaimer acknowledgment.
"""

import json
import re
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import verify_jwt, get_client_ip, _audit
from config.config_loader import (
    get_trading_params,
    get_watchlist,
    get_defaults,
    get_overrides,
    save_override,
    delete_override,
    reset_all_overrides,
    reset_section,
    invalidate_cache,
)
from db.database import get_db

logger = logging.getLogger("glyphTrader.api.trade_settings")
router = APIRouter(prefix="/api/trade-settings", tags=["trade-settings"])


class ParamsUpdate(BaseModel):
    """Section-keyed JSON body for trading params overrides."""
    overrides: Dict[str, Any]


class StockUpdate(BaseModel):
    symbol: str
    sector: str = "Custom"
    tier: int = 3
    v4_threshold: float = 75.0
    benchmark_index: str = "SPY"
    tier_size_multiplier: float = 1.0


# --- Validation ---

PARAM_RANGES = {
    "position_sizing": {
        "initial_pct": (1.0, 50.0),
        "pyramid_pct": (1.0, 30.0),
        "max_per_stock_pct": (5.0, 100.0),
    },
    "pyramid": {
        "v4_min_pyramid_score": (50.0, 100.0),
        "max_pyramids_per_position": (0, 5),
    },
    "atr_exits": {
        "stop_loss_mult": (1.0, 10.0),
        "t1_target_mult": (0.1, 5.0),
        "t2_target_mult": (0.5, 10.0),
        "t3_target_mult": (1.0, 20.0),
        "t1_exit_pct": (1, 100),
        "t2_exit_pct": (0, 100),
        "t3_exit_pct": (0, 100),
    },
    "filters": {
        "ema_fast": (2, 50),
        "ema_slow": (5, 200),
        "price_movement_atr_mult": (0.1, 3.0),
        "max_slippage_pct": (0.5, 20.0),
        "regime_vix_max": (20, 50),
        "regime_vix_allow_below": (10, 30),
        "regime_sma_period": (20, 300),
    },
    "stepped_stops": {
        "step_size": (0.1, 2.0),
    },
    "breakeven": {
        "offset_pct": (0.1, 5.0),
        "cap_at_t1_minus_pct": (0.1, 5.0),
    },
    "time_stops": {
        "stagnant_win_days": (5, 120),
        "stagnant_win_min_profit_pct": (1.0, 20.0),
        "hard_time_stop_days": (10, 365),
    },
}


def _validate_params(overrides: Dict[str, Any]) -> List[str]:
    """Validate param overrides. Returns list of error messages."""
    errors = []

    for section_key, section_val in overrides.items():
        if not isinstance(section_val, dict):
            # Top-level scalars (e.g., vix_sizing_brackets)
            continue

        if section_key not in PARAM_RANGES:
            continue

        ranges = PARAM_RANGES[section_key]
        for field, value in section_val.items():
            if field in ranges:
                lo, hi = ranges[field]
                if not (lo <= value <= hi):
                    errors.append(f"{section_key}.{field}: {value} out of range [{lo}, {hi}]")

    # Cross-field validations
    atr = overrides.get("atr_exits", {})
    if atr:
        t1 = atr.get("t1_exit_pct")
        t2 = atr.get("t2_exit_pct")
        t3 = atr.get("t3_exit_pct")
        if t1 is not None and t2 is not None and t3 is not None:
            if t1 + t2 + t3 != 100:
                errors.append(f"atr_exits: t1_exit_pct + t2_exit_pct + t3_exit_pct must equal 100 (got {t1 + t2 + t3})")

        t1m = atr.get("t1_target_mult")
        t2m = atr.get("t2_target_mult")
        t3m = atr.get("t3_target_mult")
        mults = [v for v in [t1m, t2m, t3m] if v is not None]
        if len(mults) >= 2:
            # Need to check ordering with defaults for partial overrides
            defaults = get_defaults()["trading_params"]["atr_exits"]
            full_t1 = t1m if t1m is not None else defaults["t1_target_mult"]
            full_t2 = t2m if t2m is not None else defaults["t2_target_mult"]
            full_t3 = t3m if t3m is not None else defaults["t3_target_mult"]
            if not (full_t1 < full_t2 < full_t3):
                errors.append(f"atr_exits: target multipliers must be t1 < t2 < t3 ({full_t1}, {full_t2}, {full_t3})")

    filters = overrides.get("filters", {})
    if filters:
        defaults_f = get_defaults()["trading_params"]["filters"]
        ema_fast = filters.get("ema_fast", defaults_f["ema_fast"])
        ema_slow = filters.get("ema_slow", defaults_f["ema_slow"])
        if ema_fast >= ema_slow:
            errors.append(f"filters: ema_fast ({ema_fast}) must be < ema_slow ({ema_slow})")

        vix_max = filters.get("regime_vix_max", defaults_f["regime_vix_max"])
        vix_below = filters.get("regime_vix_allow_below", defaults_f["regime_vix_allow_below"])
        if vix_below >= vix_max:
            errors.append(f"filters: regime_vix_allow_below ({vix_below}) must be < regime_vix_max ({vix_max})")

        blocked = filters.get("blocked_tiers")
        if blocked is not None:
            for t in blocked:
                if t not in (1, 2, 3, 4, 5):
                    errors.append(f"filters: blocked_tiers values must be 1-5 (got {t})")

    # VIX sizing brackets validation
    brackets = overrides.get("vix_sizing_brackets")
    multipliers = overrides.get("vix_sizing_multipliers")
    if brackets is not None:
        if not all(brackets[i] < brackets[i + 1] for i in range(len(brackets) - 1)):
            errors.append("vix_sizing_brackets must be ascending")
    if multipliers is not None:
        if not all(multipliers[i] >= multipliers[i + 1] for i in range(len(multipliers) - 1)):
            errors.append("vix_sizing_multipliers must be descending")
    if brackets is not None and multipliers is not None:
        if len(multipliers) != len(brackets) + 1:
            errors.append(f"vix_sizing_multipliers length ({len(multipliers)}) must be brackets length + 1 ({len(brackets) + 1})")

    return errors


def _validate_stock(stock: StockUpdate) -> List[str]:
    errors = []
    if not re.match(r"^[A-Z]{1,5}$", stock.symbol):
        errors.append("symbol must be 1-5 uppercase letters")
    if stock.tier not in (1, 2, 3, 4, 5):
        errors.append("tier must be 1-5")
    if not (50.0 <= stock.v4_threshold <= 100.0):
        errors.append("v4_threshold must be 50-100")
    if stock.benchmark_index not in ("SPY", "QQQ"):
        errors.append("benchmark_index must be SPY or QQQ")
    if not (0.1 <= stock.tier_size_multiplier <= 3.0):
        errors.append("tier_size_multiplier must be 0.1-3.0")
    return errors


# --- Endpoints ---

@router.get("/")
def get_merged_config(user: str = Depends(verify_jwt)):
    """Return full merged config with override indicators."""
    params = get_trading_params()
    watchlist = get_watchlist()
    overrides = get_overrides()

    return {
        "trading_params": params,
        "watchlist": watchlist,
        "overrides": overrides,
    }


@router.get("/defaults")
def get_default_config(user: str = Depends(verify_jwt)):
    """Return raw JSON defaults only."""
    return get_defaults()


@router.get("/overrides")
def get_override_config(user: str = Depends(verify_jwt)):
    """Return raw DB overrides only."""
    return get_overrides()


@router.put("/params")
def update_params(body: ParamsUpdate, request: Request, user: str = Depends(verify_jwt)):
    """Update trading params (section-keyed JSON body)."""
    ip = get_client_ip(request)

    errors = _validate_params(body.overrides)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    for section_key, section_val in body.overrides.items():
        save_override(section_key, section_val, "trading_params")

    with get_db() as conn:
        _audit(conn, "config_update", ip, {"sections": list(body.overrides.keys())})

    return {"message": "Config updated", "sections": list(body.overrides.keys())}


@router.put("/watchlist/stock")
def upsert_stock(stock: StockUpdate, request: Request, user: str = Depends(verify_jwt)):
    """Add or modify a stock in the watchlist."""
    ip = get_client_ip(request)

    errors = _validate_stock(stock)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    # Check if stock exists in defaults
    defaults = get_defaults()["watchlist"]
    default_symbols = {s["symbol"] for s in defaults["stocks"]}
    overrides = get_overrides()["watchlist"]

    stock_data = {
        "sector": stock.sector,
        "tier": stock.tier,
        "v4_threshold": stock.v4_threshold,
        "benchmark_index": stock.benchmark_index,
        "tier_size_multiplier": stock.tier_size_multiplier,
    }

    if stock.symbol in default_symbols:
        # Modify existing — store as watchlist_modify delta
        mods = overrides.get("watchlist_modify", {})
        mods[stock.symbol] = stock_data
        save_override("watchlist_modify", mods, "watchlist")
    else:
        # Add new stock
        adds = overrides.get("watchlist_add", [])
        # Remove if already in adds list
        adds = [a for a in adds if a["symbol"] != stock.symbol]
        adds.append({"symbol": stock.symbol, **stock_data})
        save_override("watchlist_add", adds, "watchlist")

    # If it was previously removed, un-remove it
    removals = overrides.get("watchlist_remove", [])
    if stock.symbol in removals:
        removals.remove(stock.symbol)
        if removals:
            save_override("watchlist_remove", removals, "watchlist")
        else:
            delete_override("watchlist_remove", "watchlist")

    with get_db() as conn:
        _audit(conn, "watchlist_update", ip, {"symbol": stock.symbol, "action": "upsert"})

    return {"message": f"Stock {stock.symbol} updated"}


@router.delete("/watchlist/stock/{symbol}")
def remove_stock(symbol: str, request: Request, user: str = Depends(verify_jwt)):
    """Remove a stock from the watchlist."""
    ip = get_client_ip(request)
    symbol = symbol.upper()

    overrides = get_overrides()["watchlist"]
    defaults = get_defaults()["watchlist"]
    default_symbols = {s["symbol"] for s in defaults["stocks"]}

    if symbol in default_symbols:
        # Add to removals list
        removals = overrides.get("watchlist_remove", [])
        if symbol not in removals:
            removals.append(symbol)
            save_override("watchlist_remove", removals, "watchlist")

        # Also remove from modifications if present
        mods = overrides.get("watchlist_modify", {})
        if symbol in mods:
            del mods[symbol]
            if mods:
                save_override("watchlist_modify", mods, "watchlist")
            else:
                delete_override("watchlist_modify", "watchlist")
    else:
        # Remove from additions list
        adds = overrides.get("watchlist_add", [])
        adds = [a for a in adds if a["symbol"] != symbol]
        save_override("watchlist_add", adds, "watchlist")

    invalidate_cache()

    with get_db() as conn:
        _audit(conn, "watchlist_update", ip, {"symbol": symbol, "action": "remove"})

    return {"message": f"Stock {symbol} removed"}


@router.post("/reset")
def reset_all(request: Request, user: str = Depends(verify_jwt)):
    """Reset ALL overrides (back to JSON defaults)."""
    ip = get_client_ip(request)
    reset_all_overrides()

    with get_db() as conn:
        _audit(conn, "config_reset", ip, {"scope": "all"})

    return {"message": "All overrides reset to defaults"}


@router.post("/reset/{section}")
def reset_one_section(section: str, request: Request, user: str = Depends(verify_jwt)):
    """Reset one section of trading params."""
    ip = get_client_ip(request)

    valid_sections = [
        "position_sizing", "pyramid", "atr_exits", "filters",
        "stepped_stops", "breakeven", "time_stops",
        "vix_sizing_brackets", "vix_sizing_multipliers",
    ]
    if section == "watchlist":
        # Reset all watchlist overrides
        for key in ["watchlist_modify", "watchlist_remove", "watchlist_add"]:
            delete_override(key, "watchlist")
        invalidate_cache()
    elif section in valid_sections:
        reset_section(section)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown section: {section}")

    with get_db() as conn:
        _audit(conn, "config_reset", ip, {"scope": section})

    return {"message": f"Section '{section}' reset to defaults"}


@router.get("/disclaimer")
def check_disclaimer(user: str = Depends(verify_jwt)):
    """Check if disclaimer has been acknowledged."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'disclaimer_acknowledged'"
        ).fetchone()
    acknowledged = row is not None and row["value"] == "true"
    return {"acknowledged": acknowledged}


@router.post("/disclaimer")
def acknowledge_disclaimer(request: Request, user: str = Depends(verify_jwt)):
    """Record disclaimer acknowledgment."""
    ip = get_client_ip(request)
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("disclaimer_acknowledged", "true", now),
        )
        _audit(conn, "disclaimer_acknowledged", ip)

    return {"acknowledged": True}
