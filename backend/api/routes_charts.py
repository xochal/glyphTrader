"""
Chart data API: equity curve, benchmark comparison.
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional

from api.auth import verify_jwt
from db.database import get_db

router = APIRouter(prefix="/api/charts", tags=["charts"])


@router.get("/equity")
def get_equity_curve(user: str = Depends(verify_jwt), days: int = Query(90, ge=0)):
    with get_db() as conn:
        if days == 0:
            rows = conn.execute(
                "SELECT date, account_value_cents, cash_cents, positions_value_cents, "
                "daily_pnl_cents, spy_close_cents, qqq_close_cents "
                "FROM portfolio_snapshots ORDER BY date DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT date, account_value_cents, cash_cents, positions_value_cents, "
                "daily_pnl_cents, spy_close_cents, qqq_close_cents "
                "FROM portfolio_snapshots ORDER BY date DESC LIMIT ?",
                (days,),
            ).fetchall()

    if not rows:
        return {"data": []}

    rows = list(reversed(rows))
    base_account = rows[0]["account_value_cents"] if rows[0]["account_value_cents"] else 1
    base_spy = rows[0]["spy_close_cents"] if rows[0]["spy_close_cents"] else 1
    base_qqq = rows[0]["qqq_close_cents"] if rows[0]["qqq_close_cents"] else 1

    data = []
    for r in rows:
        acct = r["account_value_cents"] or base_account
        spy = r["spy_close_cents"] or base_spy
        qqq = r["qqq_close_cents"] or base_qqq
        data.append({
            "date": r["date"],
            "account_pct": round((acct / base_account - 1) * 100, 2),
            "spy_pct": round((spy / base_spy - 1) * 100, 2),
            "qqq_pct": round((qqq / base_qqq - 1) * 100, 2),
            "account_value": round(acct / 100, 2),
            "daily_pnl": round((r["daily_pnl_cents"] or 0) / 100, 2),
        })
    return {"data": data}
