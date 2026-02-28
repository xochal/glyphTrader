"""
Dashboard API: positions, P&L, account stats, today's signals.
"""

import json
import logging
from datetime import datetime, timezone, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import verify_jwt
from db.database import get_db
from db import crypto

logger = logging.getLogger("glyphTrader.api.dashboard")
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _cents_to_dollars(cents: Optional[int]) -> Optional[float]:
    return round(cents / 100, 2) if cents is not None else None


@router.get("/positions")
def get_positions(user: str = Depends(verify_jwt), trade_type: Optional[str] = Query(None)):
    query = "SELECT * FROM trades WHERE status = 'open'"
    params = []
    if trade_type in ("auto", "manual"):
        query += " AND trade_type = ?"
        params.append(trade_type)
    query += " ORDER BY created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    now = datetime.now(timezone.utc)
    positions = []
    for r in rows:
        days_held = None
        if r["entry_time"]:
            try:
                entry_dt = datetime.fromisoformat(r["entry_time"])
                days_held = (now - entry_dt).days
            except (ValueError, TypeError):
                pass

        trade_type_val = r["trade_type"] if "trade_type" in r.keys() else "auto"
        pos = {
            "id": r["id"],
            "symbol": r["symbol"],
            "trade_type": trade_type_val,
            "shares": r["shares"],
            "shares_remaining": r["shares_remaining"],
            "entry_price": _cents_to_dollars(r["entry_price_cents"]),
            "blended_entry_price": _cents_to_dollars(r["blended_entry_price_cents"]),
            "stop_price": _cents_to_dollars(r["stop_price_cents"]),
            "base_stop": _cents_to_dollars(r["base_stop_cents"]),
            "target_t1": _cents_to_dollars(r["target_t1_price_cents"]),
            "target_t2": _cents_to_dollars(r["target_t2_price_cents"]),
            "target_t3": _cents_to_dollars(r["target_t3_price_cents"]),
            "t1_filled": bool(r["t1_filled"]),
            "t2_filled": bool(r["t2_filled"]),
            "t3_filled": bool(r["t3_filled"]),
            "state": r["position_state"],
            "pyramid_count": r["pyramid_count"],
            "entry_time": r["entry_time"],
            "days_held": days_held,
            "original_atr": _cents_to_dollars(r["original_atr_cents"]),
        }

        # Include manual trade fields if present
        if trade_type_val == "manual":
            pos.update({
                "stop_mode": r["stop_mode"] if "stop_mode" in r.keys() else None,
                "ratchet_enabled": bool(r["ratchet_enabled"]) if "ratchet_enabled" in r.keys() else False,
                "targets_enabled": bool(r["targets_enabled"]) if "targets_enabled" in r.keys() else True,
                "ratchet_mode": r["ratchet_mode"] if "ratchet_mode" in r.keys() else None,
            })

        positions.append(pos)
    return {"positions": positions}


def _compute_stats(conn, trade_type_filter: str = None) -> dict:
    """Compute closed trade stats, optionally filtered by trade_type."""
    base = "SELECT COUNT(*) as total, " \
           "SUM(CASE WHEN realized_pnl_cents > 0 THEN 1 ELSE 0 END) as wins, " \
           "SUM(realized_pnl_cents) as total_pnl " \
           "FROM trades WHERE status = 'closed'"
    params = []
    if trade_type_filter:
        base += " AND trade_type = ?"
        params.append(trade_type_filter)
    row = conn.execute(base, params).fetchone()
    total_closed = row["total"] or 0
    wins = row["wins"] or 0
    total_pnl = row["total_pnl"] or 0
    win_rate = round(wins / total_closed * 100, 1) if total_closed > 0 else 0
    return {
        "total_closed": total_closed,
        "wins": wins,
        "win_rate": win_rate,
        "total_pnl": _cents_to_dollars(total_pnl),
    }


@router.get("/stats")
def get_stats(user: str = Depends(verify_jwt)):
    with get_db() as conn:
        open_count = conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE status = 'open'").fetchone()["cnt"]
        open_auto = conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE status = 'open' AND trade_type = 'auto'").fetchone()["cnt"]
        open_manual = conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE status = 'open' AND trade_type = 'manual'").fetchone()["cnt"]

        combined = _compute_stats(conn)
        auto_stats = _compute_stats(conn, "auto")
        manual_stats = _compute_stats(conn, "manual")

        latest_snap = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()

    account_value = _cents_to_dollars(latest_snap["account_value_cents"]) if latest_snap else None
    cash = _cents_to_dollars(latest_snap["cash_cents"]) if latest_snap else None
    daily_pnl = _cents_to_dollars(latest_snap["daily_pnl_cents"]) if latest_snap else None

    return {
        "open_positions": open_count,
        "open_auto": open_auto,
        "open_manual": open_manual,
        "total_closed": combined["total_closed"],
        "win_rate": combined["win_rate"],
        "total_pnl": combined["total_pnl"],
        "account_value": account_value,
        "cash": cash,
        "daily_pnl": daily_pnl,
        "auto_stats": auto_stats,
        "manual_stats": manual_stats,
    }


@router.get("/signals")
def get_todays_signals(user: str = Depends(verify_jwt)):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_plans WHERE date = ? ORDER BY v4_score DESC",
            (today,),
        ).fetchall()
    signals = []
    for r in rows:
        signals.append({
            "symbol": r["symbol"],
            "v4_score": r["v4_score"],
            "action": r["action"],
            "skip_reason": r["skip_reason"],
            "entry_price": _cents_to_dollars(r["entry_price_cents"]),
            "stop_price": _cents_to_dollars(r["stop_price_cents"]),
            "t1_price": _cents_to_dollars(r["t1_price_cents"]),
            "t2_price": _cents_to_dollars(r["t2_price_cents"]),
            "t3_price": _cents_to_dollars(r["t3_price_cents"]),
            "shares": r["shares"],
        })
    return {"date": today, "signals": signals}


@router.get("/regime")
def get_regime_status(user: str = Depends(verify_jwt)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM regime_state WHERE id = 1").fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Regime state not found")

    vix = row["vix_level"]
    spy_above = bool(row["spy_above_sma100"])
    qqq_above = bool(row["qqq_above_sma100"]) if "qqq_above_sma100" in row.keys() else False
    allows = bool(row["regime_allows_entry"])

    if vix is not None and vix >= 32:
        regime_type = "PANIC"
    elif allows:
        regime_type = "FAVORABLE"
    else:
        regime_type = "UNFAVORABLE"

    return {
        "vix_level": round(vix, 2) if vix is not None else None,
        "spy_above_sma100": spy_above,
        "qqq_above_sma100": qqq_above,
        "regime_allows_entry": allows,
        "regime_type": regime_type,
        "updated_at": row["updated_at"],
    }
