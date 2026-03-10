"""
APScheduler jobs: all times ET, coalesce=True, max_instances=1.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db import crypto

logger = logging.getLogger("glyphTrader.scheduler")
ET = ZoneInfo("America/New_York")

scheduler = AsyncIOScheduler(timezone=ET)
_job_status = {}


def _record_run(job_name: str, success: bool = True, error: str = None):
    _job_status[job_name] = {
        "last_run": datetime.now(ET).isoformat(),
        "success": success,
        "error": error,
    }


def job_fetch_daily_data():
    """12:30 PM ET — Fetch daily bars, calculate indicators, populate DataStore."""
    try:
        logger.info("Starting daily data fetch...")
        if crypto.is_unlocked():
            from tradier.execution import _get_tradier_client
            client = _get_tradier_client()
        else:
            from tradier.execution import _get_degraded_client
            client = _get_degraded_client()

        from data.market_data import fetch_all_watchlist
        from data.enrichment import enrich_and_store

        raw_data = fetch_all_watchlist(client)
        success = enrich_and_store(raw_data)
        if not success:
            _record_run("fetch_daily_data", False, "Data quality below threshold")
            return
        _record_run("fetch_daily_data")
        logger.info("Daily data fetch complete")
    except Exception as e:
        logger.error(f"Daily data fetch failed: {e}")
        _record_run("fetch_daily_data", False, str(e))


def job_generate_plans():
    """12:45 PM ET — V4 scoring -> filter cascade -> generate signals."""
    if not crypto.is_unlocked():
        logger.warning("System locked — skipping plan generation")
        _record_run("generate_plans", False, "System locked")
        return

    from db.database import get_db
    from api.auth import _get_setting
    with get_db() as conn:
        if _get_setting(conn, "observe_only") == "true":
            logger.info("Observe-only mode — skipping plan generation")
            _record_run("generate_plans", False, "Observe-only mode")
            return
        if _get_setting(conn, "trading_enabled") == "false":
            logger.info("Trading disabled (kill switch) — skipping plan generation")
            _record_run("generate_plans", False, "Kill switch active")
            return

    try:
        from core.plan_generator import generate_daily_plans
        signals = generate_daily_plans()
        _record_run("generate_plans")
        logger.info(f"Plan generation complete: {len(signals)} buy signals")
    except Exception as e:
        logger.error(f"Plan generation failed: {e}")
        _record_run("generate_plans", False, str(e))


def job_execute_trades():
    """1:00 PM ET — Process time stops, then execute new signals."""
    if not crypto.is_unlocked():
        logger.warning("System locked — skipping trade execution")
        _record_run("execute_trades", False, "System locked")
        return

    from db.database import get_db
    from api.auth import _get_setting
    with get_db() as conn:
        if _get_setting(conn, "observe_only") == "true":
            logger.info("Observe-only mode — skipping trade execution")
            _record_run("execute_trades", False, "Observe-only mode")
            return
        if _get_setting(conn, "trading_enabled") == "false":
            logger.info("Trading disabled (kill switch) — skipping trade execution")
            _record_run("execute_trades", False, "Kill switch active")
            return

    try:
        from core.plan_generator import generate_daily_plans
        from tradier.execution import execute_signals
        from db.database import get_db

        today = datetime.now(ET).strftime("%Y-%m-%d")
        with get_db() as conn:
            plans = conn.execute(
                "SELECT * FROM daily_plans WHERE date = ? AND "
                "(action = 'buy' OR (action = 'skip' AND skip_reason LIKE 'Regime:%')) "
                "ORDER BY v4_score DESC",
                (today,),
            ).fetchall()

        signals = [dict(p) for p in plans]
        count = execute_signals(signals)
        _record_run("execute_trades")
        logger.info(f"Trade execution complete: {count} entries")
    except Exception as e:
        logger.error(f"Trade execution failed: {e}")
        _record_run("execute_trades", False, str(e))


def job_monitor_cycle():
    """Every 2 min (market hours) — Combined fill detection + safety + stepped stops."""
    from db.database import get_db
    from api.auth import _get_setting
    with get_db() as conn:
        if _get_setting(conn, "observe_only") == "true":
            return  # silent — runs every 2 min, no log spam
    try:
        from tradier.safety_monitor import run_monitor_cycle
        run_monitor_cycle()
        _record_run("monitor_cycle")
    except Exception as e:
        logger.error(f"Monitor cycle failed: {e}")
        _record_run("monitor_cycle", False, str(e))


def job_reconcile_account():
    """Every 5 min (market hours) — Orphan discovery + ghost cleanup."""
    try:
        from tradier.reconciliation import run_scheduled_reconciliation
        run_scheduled_reconciliation()
        _record_run("reconcile_account")
    except Exception as e:
        logger.error(f"Reconciliation cycle failed: {e}")
        _record_run("reconcile_account", False, str(e))


def job_end_of_day():
    """4:05 PM ET — Snapshot portfolio, fetch benchmarks."""
    try:
        if crypto.is_unlocked():
            from tradier.execution import _get_tradier_client
            client = _get_tradier_client()
        else:
            from tradier.execution import _get_degraded_client
            client = _get_degraded_client()

        from data.datastore import DataStore
        from db.database import get_db

        balances = client.get_balances()
        store = DataStore()
        today = datetime.now(ET).strftime("%Y-%m-%d")
        now = datetime.now(ET).isoformat()

        spy_data = store.get_spy_data()
        qqq_data = store.get_qqq_data()
        spy_close = round(spy_data["close"].iloc[-1] * 100) if spy_data is not None and not spy_data.empty else None
        qqq_close = round(qqq_data["close"].iloc[-1] * 100) if qqq_data is not None and not qqq_data.empty else None

        account_value = round(float(balances.get("total_equity", 0)) * 100)
        cash = round(float(balances.get("total_cash", 0)) * 100)

        # Get previous snapshot for daily P&L
        with get_db() as conn:
            prev = conn.execute(
                "SELECT account_value_cents FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
            ).fetchone()
            prev_value = prev["account_value_cents"] if prev else account_value
            daily_pnl = account_value - prev_value

            conn.execute(
                "INSERT INTO portfolio_snapshots (date, account_value_cents, cash_cents, "
                "positions_value_cents, daily_pnl_cents, spy_close_cents, qqq_close_cents, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (today, account_value, cash, account_value - cash, daily_pnl, spy_close, qqq_close, now),
            )

            # Update regime state — read thresholds from config (not hardcoded)
            from config.config_loader import get_trading_params
            tp = get_trading_params()
            vix_max = tp["filters"]["regime_vix_max"]
            vix_allow_below = tp["filters"]["regime_vix_allow_below"]
            vix_level = store.get_vix_level()
            spy_above = 1 if store.get_spy_sma100() else 0
            qqq_above = 1 if store.get_qqq_sma100() else 0
            index_ok = spy_above or qqq_above
            allows = 1 if vix_level and vix_level < vix_max and (index_ok or vix_level < vix_allow_below) else 0
            conn.execute(
                "UPDATE regime_state SET vix_level = ?, spy_above_sma100 = ?, "
                "qqq_above_sma100 = ?, regime_allows_entry = ?, updated_at = ? WHERE id = 1",
                (vix_level, spy_above, qqq_above, allows, now),
            )

            # Prune stale order_state records for closed trades
            # (rejected/cancelled records accumulate from retry loops)
            pruned = conn.execute(
                "DELETE FROM order_state WHERE trade_id IN "
                "(SELECT id FROM trades WHERE status NOT IN ('open')) "
                "AND status IN ('rejected', 'cancelled', 'canceled')"
            ).rowcount
            if pruned:
                logger.info(f"Pruned {pruned} stale order_state records from closed trades")

        _record_run("end_of_day")
        logger.info(f"EOD snapshot: equity=${account_value/100:.2f}, daily P&L=${daily_pnl/100:.2f}")
    except Exception as e:
        logger.error(f"End of day failed: {e}")
        _record_run("end_of_day", False, str(e))


def start_scheduler():
    """Configure and start all scheduled jobs."""
    common = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}

    # 12:30 PM ET — fetch data
    scheduler.add_job(
        job_fetch_daily_data, CronTrigger(hour=12, minute=30, day_of_week="mon-fri", timezone=ET),
        id="fetch_daily_data", **common,
    )

    # 12:45 PM ET — generate plans
    scheduler.add_job(
        job_generate_plans, CronTrigger(hour=12, minute=45, day_of_week="mon-fri", timezone=ET),
        id="generate_plans", **common,
    )

    # 1:00 PM ET — execute trades
    scheduler.add_job(
        job_execute_trades, CronTrigger(hour=13, minute=0, day_of_week="mon-fri", timezone=ET),
        id="execute_trades", **common,
    )

    # Every 2 min, 9:30 AM - 4:00 PM ET, weekdays
    scheduler.add_job(
        job_monitor_cycle, CronTrigger(
            minute="*/2", hour="9-15", day_of_week="mon-fri", timezone=ET,
        ),
        id="monitor_cycle", **common,
    )
    # Also cover 4:00 PM
    scheduler.add_job(
        job_monitor_cycle, CronTrigger(
            minute="0,2,4", hour=16, day_of_week="mon-fri", timezone=ET,
        ),
        id="monitor_cycle_close", **common,
    )

    # Every 5 min, 9:30 AM - 4:00 PM ET, weekdays — reconciliation
    scheduler.add_job(
        job_reconcile_account, CronTrigger(
            minute="*/5", hour="9-15", day_of_week="mon-fri", timezone=ET,
        ),
        id="reconcile_account", **common,
    )
    scheduler.add_job(
        job_reconcile_account, CronTrigger(
            minute="0,5", hour=16, day_of_week="mon-fri", timezone=ET,
        ),
        id="reconcile_account_close", **common,
    )

    # 4:05 PM ET — end of day
    scheduler.add_job(
        job_end_of_day, CronTrigger(hour=16, minute=5, day_of_week="mon-fri", timezone=ET),
        id="end_of_day", **common,
    )

    scheduler.start()
    logger.info("Scheduler started with 7 jobs")


def get_scheduler_status() -> dict:
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "last_run": _job_status.get(job.id, {}).get("last_run"),
            "success": _job_status.get(job.id, {}).get("success"),
        })
    return {"running": scheduler.running, "jobs": jobs}
