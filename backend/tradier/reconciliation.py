"""
Reconciliation: startup + scheduled.
Ghost detection, orphan discovery, FLATTEN_PENDING recovery.
Works in degraded mode.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List

from db.database import get_db
from db import crypto

logger = logging.getLogger("glyphTrader.reconciliation")

# Module-level orphan cache for API reads
_orphan_cache: List[Dict] = []


def _get_client():
    """Get Tradier client — uses degraded token if locked."""
    if crypto.is_unlocked():
        from tradier.execution import _get_tradier_client
        return _get_tradier_client()
    else:
        from tradier.execution import _get_degraded_client
        return _get_degraded_client()


def get_orphan_cache() -> List[Dict]:
    """Return the current orphan cache for API consumption."""
    return list(_orphan_cache)


def dismiss_orphan(symbol: str):
    """Insert into dismissed_orphans; auto-clear if Tradier no longer holds."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dismissed_orphans (symbol, dismissed_at) VALUES (?, ?)",
            (symbol, now),
        )
    # Remove from cache immediately
    global _orphan_cache
    _orphan_cache = [o for o in _orphan_cache if o["symbol"] != symbol]
    logger.info(f"Dismissed orphan: {symbol}")


def run_startup_reconciliation():
    """
    Run on container start BEFORE scheduler begins.
    1. Check DataStore freshness
    2. Verify open positions have protection orders
    3. Detect ghost positions (DB open, Tradier none)
    4. Detect orphan positions (Tradier has, DB doesn't)
    5. Recover FLATTEN_PENDING trades
    6. Log kill switch and lock state
    """
    logger.info("Starting reconciliation...")

    try:
        client = _get_client()
    except Exception as e:
        logger.warning(f"Cannot create Tradier client for reconciliation: {e}")
        return

    # Load processed fills from DB (Review Finding #1)
    from tradier.safety_monitor import load_processed_fills
    load_processed_fills()

    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        # Get open trades from DB
        db_trades = conn.execute(
            "SELECT * FROM trades WHERE status = 'open'"
        ).fetchall()
        db_symbols = {t["symbol"] for t in db_trades}

        # Get positions from Tradier
        try:
            tradier_positions = client.get_positions()
            tradier_symbols = {p["symbol"] for p in tradier_positions}
        except Exception as e:
            logger.error(f"Failed to fetch Tradier positions: {e}")
            tradier_symbols = set()
            tradier_positions = []

        # Ghost positions: DB says open, Tradier has no position
        # Skip ADOPTING and FLATTEN_PENDING states
        ghosts = db_symbols - tradier_symbols
        for symbol in ghosts:
            ghost_trades = [t for t in db_trades if t["symbol"] == symbol]
            for t in ghost_trades:
                if t["position_state"] in ("ADOPTING", "FLATTEN_PENDING"):
                    logger.info(f"Skipping ghost cleanup for {symbol} (state={t['position_state']})")
                    continue
                logger.warning(f"Ghost position detected: {symbol} (DB open, not on Tradier)")
                conn.execute(
                    "UPDATE trades SET status = 'closed', exit_reason = 'ghost_cleanup', "
                    "position_state = 'CLOSED', close_time = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'open'",
                    (now, now, t["id"]),
                )
                conn.execute(
                    "UPDATE order_state SET status = 'cancelled', updated_at = ? "
                    "WHERE trade_id = ? AND status = 'open'",
                    (now, t["id"]),
                )

        # Orphan positions: Tradier has position, DB doesn't
        orphans = tradier_symbols - db_symbols
        global _orphan_cache
        startup_orphans = []
        dismissed = {
            r["symbol"]
            for r in conn.execute("SELECT symbol FROM dismissed_orphans").fetchall()
        }
        for symbol in orphans:
            logger.warning(f"Orphan position on Tradier: {symbol} — not tracked in DB")
            pos = next((p for p in tradier_positions if p["symbol"] == symbol), None)
            if pos and symbol not in dismissed:
                qty = int(pos.get("quantity", 0))
                if qty > 0:
                    cost_basis = float(pos.get("cost_basis", 0))
                    avg_cost = cost_basis / qty if qty > 0 else 0
                    startup_orphans.append({
                        "symbol": symbol,
                        "quantity": qty,
                        "cost_basis": round(avg_cost, 2),
                        "cost_basis_cents": round(avg_cost * 100),
                    })
        _orphan_cache = startup_orphans

        # Recover FLATTEN_PENDING trades
        flatten_pending = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND position_state = 'FLATTEN_PENDING'"
        ).fetchall()
        for trade in flatten_pending:
            _recover_flatten_pending(client, conn, trade, now)

        # Verify protection orders for remaining open trades
        for trade in db_trades:
            if trade["symbol"] in ghosts:
                continue
            if trade["position_state"] in ("ENTRY_PENDING", "CLOSED", "ADOPTING", "FLATTEN_PENDING"):
                continue
            open_orders = conn.execute(
                "SELECT COUNT(*) as cnt FROM order_state WHERE trade_id = ? AND status = 'open'",
                (trade["id"],),
            ).fetchone()
            if open_orders["cnt"] == 0 and trade["shares_remaining"] > 0:
                logger.warning(f"No protection orders for {trade['symbol']} — will be restored by monitor cycle")

        # Log system state
        kill_switch = conn.execute(
            "SELECT value FROM settings WHERE key = 'trading_enabled'"
        ).fetchone()
        trading = kill_switch["value"] if kill_switch else "unknown"
        locked = not crypto.is_unlocked()

        logger.info(f"Reconciliation complete: {len(db_trades)} open trades, {len(ghosts)} ghosts cleaned, {len(orphans)} orphans found")
        logger.info(f"System state: trading_enabled={trading}, locked={locked}")
        if locked:
            logger.warning("SYSTEM LOCKED — log in to unlock full functionality")


def run_scheduled_reconciliation():
    """
    Run every 5 min during market hours.
    Hardened ghost cleanup, orphan discovery with quantity awareness.
    """
    global _orphan_cache

    try:
        client = _get_client()
    except Exception as e:
        logger.error(f"Cannot create Tradier client for scheduled reconciliation: {e}")
        return

    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        db_trades = conn.execute(
            "SELECT * FROM trades WHERE status = 'open'"
        ).fetchall()

        try:
            tradier_positions = client.get_positions()
        except Exception as e:
            logger.error(f"Failed to fetch Tradier positions: {e}")
            return

        tradier_by_symbol: Dict[str, Dict] = {}
        for p in tradier_positions:
            sym = p["symbol"]
            qty = int(p.get("quantity", 0))
            # Filter out short positions (M1)
            if qty <= 0:
                continue
            tradier_by_symbol[sym] = p

        db_by_symbol: Dict[str, List] = {}
        for t in db_trades:
            db_by_symbol.setdefault(t["symbol"], []).append(t)

        # Ghost detection: DB open, Tradier has nothing
        # FLAT-H1 guard: if Tradier returns empty but DB has 2+ trades, skip ghost cleanup
        db_open_count = len(db_trades)
        if not tradier_positions and db_open_count >= 2:
            logger.warning(
                f"Tradier returned 0 positions but DB has {db_open_count} open trades — "
                f"skipping ghost cleanup (possible API issue)"
            )
        else:
            db_symbols = set(db_by_symbol.keys())
            tradier_symbols = set(tradier_by_symbol.keys())
            ghosts = db_symbols - tradier_symbols

            for symbol in ghosts:
                for t in db_by_symbol[symbol]:
                    if t["position_state"] in ("ADOPTING", "FLATTEN_PENDING"):
                        continue
                    logger.warning(f"Scheduled ghost cleanup: {symbol} (trade {t['id']})")
                    conn.execute(
                        "UPDATE trades SET status = 'closed', exit_reason = 'ghost_cleanup', "
                        "position_state = 'CLOSED', close_time = ?, updated_at = ? "
                        "WHERE id = ? AND status = 'open'",
                        (now, now, t["id"]),
                    )
                    conn.execute(
                        "UPDATE order_state SET status = 'cancelled', updated_at = ? "
                        "WHERE trade_id = ? AND status = 'open'",
                        (now, t["id"]),
                    )

        # Orphan detection: Tradier has shares that DB doesn't track
        # H3 fix: partial quantity awareness
        dismissed = {
            r["symbol"]
            for r in conn.execute("SELECT symbol FROM dismissed_orphans").fetchall()
        }

        new_orphans = []
        for sym, pos in tradier_by_symbol.items():
            tradier_qty = int(pos.get("quantity", 0))
            db_shares = sum(
                t["shares_remaining"] for t in db_by_symbol.get(sym, [])
            )
            orphan_qty = tradier_qty - db_shares
            if orphan_qty > 0 and sym not in dismissed:
                cost_basis = float(pos.get("cost_basis", 0))
                # Tradier cost_basis is TOTAL, not per-share
                avg_cost = cost_basis / tradier_qty if tradier_qty > 0 else 0
                new_orphans.append({
                    "symbol": sym,
                    "quantity": orphan_qty,
                    "cost_basis": round(avg_cost, 2),
                    "cost_basis_cents": round(avg_cost * 100),
                })

            # Quantity mismatch warning
            if db_shares > 0 and tradier_qty != db_shares:
                logger.warning(
                    f"Quantity mismatch: {sym} Tradier={tradier_qty} DB={db_shares}"
                )

        # Auto-clear dismissed orphans that Tradier no longer holds
        for sym in list(dismissed):
            if sym not in tradier_by_symbol:
                conn.execute("DELETE FROM dismissed_orphans WHERE symbol = ?", (sym,))
                logger.info(f"Auto-cleared dismissed orphan {sym} (no longer on Tradier)")

        _orphan_cache = new_orphans

        if new_orphans:
            logger.info(f"Orphans discovered: {[o['symbol'] for o in new_orphans]}")


def _recover_flatten_pending(client, conn, trade, now):
    """Check if flatten sell orders have filled; close trade if so."""
    trade_id = trade["id"]
    flatten_orders = conn.execute(
        "SELECT * FROM order_state WHERE trade_id = ? AND order_type = 'flatten_sell' AND status = 'open'",
        (trade_id,),
    ).fetchall()

    if not flatten_orders:
        logger.warning(f"FLATTEN_PENDING trade {trade['symbol']} has no flatten_sell orders")
        return

    for order in flatten_orders:
        try:
            tradier_order = client.get_order(int(order["order_id"]))
            status = tradier_order.get("status", "")
            if status == "filled":
                fill_price = tradier_order.get("avg_fill_price", 0)
                fill_cents = round(float(fill_price) * 100) if fill_price else 0
                entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
                pnl = (fill_cents - entry_cents) * trade["shares_remaining"]
                conn.execute(
                    "UPDATE trades SET status = 'closed', exit_reason = 'flatten', "
                    "position_state = 'CLOSED', close_time = ?, "
                    "realized_pnl_cents = realized_pnl_cents + ?, "
                    "shares_remaining = 0, updated_at = ? WHERE id = ?",
                    (now, pnl, now, trade_id),
                )
                conn.execute(
                    "UPDATE order_state SET status = 'filled', updated_at = ? WHERE id = ?",
                    (now, order["id"]),
                )
                logger.info(f"FLATTEN_PENDING recovered: {trade['symbol']} filled @ ${fill_cents/100:.2f}")
            elif status in ("cancelled", "rejected"):
                logger.warning(
                    f"FLATTEN_PENDING: {trade['symbol']} sell order {status} — "
                    f"leaving for user to retry"
                )
                conn.execute(
                    "UPDATE order_state SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, order["id"]),
                )
        except Exception as e:
            logger.error(f"Failed to check flatten order {order['order_id']}: {e}")
