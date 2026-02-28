"""
Trade history API: closed trades, stats, monthly returns.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.auth import verify_jwt
from db.database import get_db

logger = logging.getLogger("glyphTrader.api.trades")
router = APIRouter(prefix="/api/trades", tags=["trades"])


def _cents_to_dollars(cents: Optional[int]) -> Optional[float]:
    return round(cents / 100, 2) if cents is not None else None


@router.get("/history")
def get_trade_history(
    user: str = Depends(verify_jwt),
    symbol: Optional[str] = Query(None),
    result: Optional[str] = Query(None),
    trade_type: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    query = "SELECT * FROM trades WHERE status = 'closed'"
    params = []

    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.upper())
    if trade_type in ("auto", "manual"):
        query += " AND trade_type = ?"
        params.append(trade_type)
    if result == "win":
        query += " AND realized_pnl_cents > 0"
    elif result == "loss":
        query += " AND realized_pnl_cents <= 0"
    if start_date:
        query += " AND close_time >= ?"
        params.append(start_date)
    if end_date:
        query += " AND close_time <= ?"
        params.append(end_date)

    count_query = query.replace("SELECT * FROM", "SELECT COUNT(*) as cnt FROM")
    query += " ORDER BY close_time DESC LIMIT ? OFFSET ?"
    params_count = list(params)
    params.extend([limit, offset])

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        total = conn.execute(count_query, params_count).fetchone()["cnt"]

    trades = []
    for r in rows:
        entry_cents = r["blended_entry_price_cents"] or r["entry_price_cents"]
        exit_cents = r["stop_filled_price_cents"] or r["t1_filled_price_cents"]
        pnl_pct = round(r["realized_pnl_cents"] / (entry_cents * r["shares"]) * 100, 2) if entry_cents and r["shares"] else 0

        hold_days = None
        if r["entry_time"] and r["close_time"]:
            try:
                entry_dt = datetime.fromisoformat(r["entry_time"])
                close_dt = datetime.fromisoformat(r["close_time"])
                hold_days = (close_dt - entry_dt).days
            except (ValueError, TypeError):
                pass

        trade_type_val = r["trade_type"] if "trade_type" in r.keys() else "auto"
        trades.append({
            "id": r["id"],
            "symbol": r["symbol"],
            "trade_type": trade_type_val,
            "entry_price": _cents_to_dollars(entry_cents),
            "entry_time": r["entry_time"],
            "close_time": r["close_time"],
            "shares": r["shares"],
            "pnl": _cents_to_dollars(r["realized_pnl_cents"]),
            "pnl_pct": pnl_pct,
            "exit_reason": r["exit_reason"],
            "hold_days": hold_days,
            "pyramid_count": r["pyramid_count"],
        })
    return {"trades": trades, "total": total}


@router.get("/stats")
def get_trade_stats(user: str = Depends(verify_jwt), trade_type: Optional[str] = Query(None)):
    query = "SELECT realized_pnl_cents, entry_price_cents, shares FROM trades WHERE status = 'closed'"
    params = []
    if trade_type in ("auto", "manual"):
        query += " AND trade_type = ?"
        params.append(trade_type)

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return {"total_trades": 0, "win_rate": 0, "avg_win": 0, "avg_loss": 0, "profit_factor": 0, "total_pnl": 0}

    wins = [r for r in rows if r["realized_pnl_cents"] > 0]
    losses = [r for r in rows if r["realized_pnl_cents"] <= 0]
    total_pnl = sum(r["realized_pnl_cents"] for r in rows)
    gross_wins = sum(r["realized_pnl_cents"] for r in wins) if wins else 0
    gross_losses = abs(sum(r["realized_pnl_cents"] for r in losses)) if losses else 0

    return {
        "total_trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(rows) * 100, 1),
        "avg_win": _cents_to_dollars(gross_wins // len(wins)) if wins else 0,
        "avg_loss": _cents_to_dollars(gross_losses // len(losses)) if losses else 0,
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else float("inf"),
        "total_pnl": _cents_to_dollars(total_pnl),
    }


@router.get("/monthly")
def get_monthly_returns(user: str = Depends(verify_jwt), trade_type: Optional[str] = Query(None)):
    query = "SELECT close_time, realized_pnl_cents FROM trades WHERE status = 'closed' AND close_time IS NOT NULL"
    params = []
    if trade_type in ("auto", "manual"):
        query += " AND trade_type = ?"
        params.append(trade_type)

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    monthly = {}
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["close_time"])
            key = f"{dt.year}-{dt.month:02d}"
            monthly[key] = monthly.get(key, 0) + r["realized_pnl_cents"]
        except (ValueError, TypeError):
            continue

    return {"monthly": {k: _cents_to_dollars(v) for k, v in sorted(monthly.items())}}
