"""
Combined monitor cycle: fill detection + state transitions + order enforcement + stepped stops.
Runs every 2 min during market hours. Works in degraded mode.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Set, Tuple

from config.config_loader import get_trading_params
from core.position_sizer import calculate_share_distribution, calculate_exit_prices
from data.datastore import DataStore
from db.database import get_db
from db import crypto

logger = logging.getLogger("glyphTrader.safety_monitor")

# Stop price cap factor: never place a sell stop above 99.5% of current price
_STOP_PRICE_CAP_FACTOR = 0.995

# In-memory set to prevent duplicate fill processing (Review Finding #1)
processed_order_ids: Set[str] = set()

# Throttle re-injection attempts: trade_id -> (fail_count, last_fail_time)
_injection_failures: Dict[int, Tuple[int, datetime]] = {}
_INJECTION_MAX_RETRIES = 3
_INJECTION_COOLDOWN_MINUTES = 60


def _get_client():
    """Get Tradier client — uses degraded token if locked."""
    from tradier.client import TradierClient
    if crypto.is_unlocked():
        from tradier.execution import _get_tradier_client
        return _get_tradier_client()
    else:
        from tradier.execution import _get_degraded_client
        return _get_degraded_client()


def _cap_stop_price(symbol: str, stop_cents: int) -> int:
    """Cap stop price at 99.5% of current price. Prevents rejected orders when
    stop is above market (e.g. price dropped through stop level overnight).
    Returns capped stop in cents."""
    store = DataStore()
    latest = store.get_latest_indicators(symbol)
    if not latest:
        return stop_cents
    current_price_cents = round(latest["close"] * 100)
    cap = round(current_price_cents * _STOP_PRICE_CAP_FACTOR)
    if stop_cents > cap:
        logger.warning(
            f"STOP-THROUGH: {symbol} stop ${stop_cents/100:.2f} > current price "
            f"${current_price_cents/100:.2f} — capping to ${cap/100:.2f}"
        )
        return cap
    return stop_cents


def load_processed_fills():
    """Load recent fill IDs from DB on startup (Review Finding #1)."""
    global processed_order_ids
    with get_db() as conn:
        # Load order IDs from last 24 hours
        rows = conn.execute(
            "SELECT order_id FROM order_state WHERE status = 'filled' AND updated_at > datetime('now', '-1 day')"
        ).fetchall()
        processed_order_ids = {r["order_id"] for r in rows}
    logger.info(f"Loaded {len(processed_order_ids)} processed fill IDs")


def run_monitor_cycle():
    """
    Combined monitor cycle:
    1. Fill detection
    2. State transitions (bracket placement, cascades)
    3. Stepped stop ratcheting
    4. Order structure enforcement
    """
    try:
        client = _get_client()
    except Exception as e:
        logger.error(f"Cannot create Tradier client: {e}")
        return

    with get_db() as conn:
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status = 'open'"
        ).fetchall()

    if not open_trades:
        return

    # 1. Check fills
    _check_fills(client)

    # 2. Sync order statuses (detect rejected/cancelled on Tradier)
    _sync_order_statuses(client)

    # 3. Process state transitions
    _process_state_flags(client)

    # 4. Stepped stop ratcheting
    _apply_stepped_stops(client)

    # 5. Order structure enforcement
    _enforce_order_structure(client)


def _check_fills(client):
    """Detect filled orders and update trade state."""
    global processed_order_ids

    try:
        all_orders = client.get_orders()
    except Exception as e:
        logger.error(f"Failed to fetch orders: {e}")
        return

    with get_db() as conn:
        for order in all_orders:
            order_id = str(order.get("id", ""))
            if order_id in processed_order_ids:
                continue

            status = order.get("status", "")
            if status != "filled":
                continue

            # Flatten OCO/OTOCO legs (Review Finding #8)
            legs = order.get("leg", [])
            if isinstance(legs, dict):
                legs = [legs]
            if legs:
                for leg in legs:
                    _process_filled_leg(conn, order_id, leg)
            else:
                _process_filled_order(conn, order_id, order)

            processed_order_ids.add(order_id)


def _process_filled_order(conn, order_id: str, order: Dict):
    """Process a single filled order."""
    now = datetime.now(timezone.utc).isoformat()

    # Find matching order in DB
    db_order = conn.execute(
        "SELECT * FROM order_state WHERE order_id = ? AND status = 'open'",
        (order_id,),
    ).fetchone()

    if not db_order:
        # Check if it's a child of an OCO
        return

    trade_id = db_order["trade_id"]
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not trade:
        return

    order_type = db_order["order_type"]
    fill_price = order.get("avg_fill_price") or order.get("price", 0)
    fill_price_cents = round(float(fill_price) * 100) if fill_price else 0
    filled_qty = int(order.get("exec_quantity", order.get("quantity", 0)))

    # Fallback to DB order shares if Tradier didn't return fill quantity
    if filled_qty <= 0:
        filled_qty = db_order["shares"]
        logger.warning(
            f"Missing exec_quantity for order {order_id} — using DB shares: {filled_qty}"
        )

    # Fallback to DB order price if Tradier didn't return fill price
    if fill_price_cents <= 0 and db_order["price_cents"]:
        fill_price_cents = db_order["price_cents"]
        logger.warning(
            f"Missing fill price for order {order_id} — using DB price: ${fill_price_cents/100:.2f}"
        )

    # Mark order as filled
    conn.execute(
        "UPDATE order_state SET status = 'filled', updated_at = ? WHERE order_id = ?",
        (now, order_id),
    )

    if order_type in ("entry", "pyramid_entry"):
        # Entry filled
        conn.execute(
            "UPDATE trades SET position_state = 'ENTRY_FILLED', entry_price_cents = ?, updated_at = ? WHERE id = ?",
            (fill_price_cents, now, trade_id),
        )
        logger.info(f"Entry filled: {trade['symbol']} {filled_qty}sh @ ${fill_price_cents/100:.2f}")

    elif order_type == "t1_oco":
        # Determine if limit or stop filled
        side = order.get("side", "sell")
        if fill_price_cents >= trade["target_t1_price_cents"] * 0.99:
            # T1 limit hit
            _handle_t1_fill(conn, trade, fill_price_cents, filled_qty, now)
        else:
            # Stop hit
            _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now)

    elif order_type == "stop":
        _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now)

    elif order_type == "t2_oco":
        if fill_price_cents >= trade["target_t2_price_cents"] * 0.99:
            _handle_t2_fill(conn, trade, fill_price_cents, filled_qty, now)
        else:
            _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now)

    elif order_type == "t3_oco":
        if fill_price_cents >= trade["target_t3_price_cents"] * 0.99:
            _handle_t3_fill(conn, trade, fill_price_cents, filled_qty, now)
        else:
            _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now)


def _process_filled_leg(conn, parent_order_id: str, leg: Dict):
    """Process a filled leg from an OCO/OTOCO order."""
    now = datetime.now(timezone.utc).isoformat()

    # Only process the leg that actually filled (not the cancelled counterpart)
    leg_status = leg.get("status", "")
    if leg_status not in ("filled", ""):
        return

    # Find matching order
    db_order = conn.execute(
        "SELECT * FROM order_state WHERE order_id = ? AND status = 'open'",
        (parent_order_id,),
    ).fetchone()

    if not db_order:
        return

    trade_id = db_order["trade_id"]
    trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not trade:
        return

    fill_price = leg.get("avg_fill_price") or leg.get("price", 0)
    fill_price_cents = round(float(fill_price) * 100) if fill_price else 0
    filled_qty = int(leg.get("exec_quantity", leg.get("quantity", 0)))

    # Fallback to DB order shares if Tradier didn't return fill quantity
    if filled_qty <= 0:
        filled_qty = db_order["shares"]
        logger.warning(
            f"Missing exec_quantity in leg for order {parent_order_id} — "
            f"using DB shares: {filled_qty}"
        )

    # Fallback to DB order price if Tradier didn't return fill price
    if fill_price_cents <= 0 and db_order["price_cents"]:
        fill_price_cents = db_order["price_cents"]
        logger.warning(
            f"Missing fill price in leg for order {parent_order_id} — "
            f"using DB price: ${fill_price_cents/100:.2f} (may differ from actual)"
        )

    conn.execute(
        "UPDATE order_state SET status = 'filled', updated_at = ? WHERE order_id = ?",
        (now, parent_order_id),
    )

    order_type = db_order["order_type"]
    leg_type = leg.get("type", "")

    if order_type == "t1_oco":
        if leg_type == "limit":
            _handle_t1_fill(conn, trade, fill_price_cents, filled_qty, now)
        else:
            _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now)
    elif order_type == "t2_oco":
        if leg_type == "limit":
            _handle_t2_fill(conn, trade, fill_price_cents, filled_qty, now)
        else:
            _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now)
    elif order_type == "t3_oco":
        if leg_type == "limit":
            _handle_t3_fill(conn, trade, fill_price_cents, filled_qty, now)
        else:
            _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now)


def _handle_t1_fill(conn, trade, fill_price_cents, filled_qty, now):
    """T1 filled: set breakeven stop, prepare T2 cascade."""
    trade_id = trade["id"]
    remaining = trade["shares_remaining"] - filled_qty

    # Calculate breakeven stop (Review Finding #10, #11)
    entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
    params = get_trading_params()
    be_offset = params["breakeven"]["offset_pct"] / 100
    be_cap_pct = params["breakeven"]["cap_at_t1_minus_pct"] / 100

    breakeven_cents = round(entry_cents * (1 + be_offset))
    t1_cap = round(trade["target_t1_price_cents"] * (1 - be_cap_pct))
    breakeven_cents = min(breakeven_cents, t1_cap)

    # Calculate P&L for T1 portion
    t1_pnl = (fill_price_cents - entry_cents) * filled_qty

    conn.execute(
        "UPDATE trades SET t1_filled = 1, t1_filled_price_cents = ?, t1_filled_time = ?, "
        "t1_shares = ?, shares_remaining = ?, "
        "stop_price_cents = ?, base_stop_cents = ?, "  # Update base_stop on breakeven!
        "position_state = 'T1_FILLED', "
        "realized_pnl_cents = realized_pnl_cents + ?, updated_at = ? WHERE id = ?",
        (fill_price_cents, now, filled_qty, remaining, breakeven_cents, breakeven_cents,
         t1_pnl, now, trade_id),
    )

    if remaining <= 0:
        conn.execute(
            "UPDATE trades SET status = 'closed', exit_reason = 't1', close_time = ?, updated_at = ? WHERE id = ?",
            (now, now, trade_id),
        )

    logger.info(f"T1 filled: {trade['symbol']} {filled_qty}sh @ ${fill_price_cents/100:.2f}, breakeven @ ${breakeven_cents/100:.2f}")


def _handle_t2_fill(conn, trade, fill_price_cents, filled_qty, now):
    """T2 filled: lock stop to T1 level."""
    trade_id = trade["id"]
    remaining = trade["shares_remaining"] - filled_qty

    # Lock stop to T1 price after T2
    t1_price = trade["target_t1_price_cents"]
    entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
    t2_pnl = (fill_price_cents - entry_cents) * filled_qty

    conn.execute(
        "UPDATE trades SET t2_filled = 1, t2_filled_price_cents = ?, t2_filled_time = ?, "
        "t2_shares = ?, shares_remaining = ?, "
        "stop_price_cents = ?, base_stop_cents = ?, "
        "position_state = 'T2_FILLED', "
        "realized_pnl_cents = realized_pnl_cents + ?, updated_at = ? WHERE id = ?",
        (fill_price_cents, now, filled_qty, remaining, t1_price, t1_price,
         t2_pnl, now, trade_id),
    )

    if remaining <= 0:
        conn.execute(
            "UPDATE trades SET status = 'closed', exit_reason = 't2', close_time = ?, updated_at = ? WHERE id = ?",
            (now, now, trade_id),
        )

    logger.info(f"T2 filled: {trade['symbol']} {filled_qty}sh @ ${fill_price_cents/100:.2f}")


def _handle_t3_fill(conn, trade, fill_price_cents, filled_qty, now):
    """T3 filled: position fully closed."""
    trade_id = trade["id"]
    entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
    t3_pnl = (fill_price_cents - entry_cents) * filled_qty

    conn.execute(
        "UPDATE trades SET t3_filled = 1, t3_filled_price_cents = ?, t3_filled_time = ?, "
        "t3_shares = ?, shares_remaining = 0, "
        "position_state = 'T3_FILLED', status = 'closed', exit_reason = 't3', "
        "realized_pnl_cents = realized_pnl_cents + ?, close_time = ?, updated_at = ? WHERE id = ?",
        (fill_price_cents, now, filled_qty, t3_pnl, now, now, trade_id),
    )
    logger.info(f"T3 filled: {trade['symbol']} {filled_qty}sh @ ${fill_price_cents/100:.2f} — CLOSED")


def _handle_stop_fill(conn, trade, fill_price_cents, filled_qty, now):
    """Stop hit: close remaining."""
    trade_id = trade["id"]
    entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
    stop_pnl = (fill_price_cents - entry_cents) * filled_qty

    conn.execute(
        "UPDATE trades SET stop_filled = 1, stop_filled_price_cents = ?, stop_filled_time = ?, "
        "stop_shares = ?, shares_remaining = 0, "
        "position_state = 'CLOSED', status = 'closed', exit_reason = 'stop', "
        "realized_pnl_cents = realized_pnl_cents + ?, close_time = ?, updated_at = ? WHERE id = ?",
        (fill_price_cents, now, filled_qty, stop_pnl, now, now, trade_id),
    )
    logger.info(f"Stop filled: {trade['symbol']} {filled_qty}sh @ ${fill_price_cents/100:.2f} — CLOSED")


def _sync_order_statuses(client):
    """Sync DB order_state with actual Tradier statuses and order types.
    Detects rejected/cancelled orders and OCO type mismatches
    so _enforce_order_structure can re-inject proper brackets."""
    try:
        all_orders = client.get_orders()
    except Exception as e:
        logger.error(f"Failed to fetch orders for status sync: {e}")
        return

    # Build maps: order_id -> status, order_id -> class (including OCO legs)
    tradier_statuses: Dict[str, str] = {}
    tradier_classes: Dict[str, str] = {}
    for order in all_orders:
        oid = str(order.get("id", ""))
        tradier_statuses[oid] = order.get("status", "")
        tradier_classes[oid] = order.get("class", "")
        legs = order.get("leg", [])
        if isinstance(legs, dict):
            legs = [legs]
        for leg in legs:
            lid = str(leg.get("id", ""))
            tradier_statuses[lid] = leg.get("status", "")
            tradier_classes[lid] = leg.get("class", "")

    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        open_db_orders = conn.execute(
            "SELECT id, order_id, order_type, trade_id FROM order_state WHERE status = 'open'"
        ).fetchall()

        for db_order in open_db_orders:
            order_id = db_order["order_id"]
            tradier_status = tradier_statuses.get(order_id)

            # Check for status mismatches (rejected/cancelled/expired)
            if tradier_status in ("rejected", "canceled", "expired"):
                conn.execute(
                    "UPDATE order_state SET status = ?, updated_at = ? WHERE id = ?",
                    (tradier_status, now, db_order["id"]),
                )
                logger.warning(
                    f"Order {order_id} (trade {db_order['trade_id']}) synced: "
                    f"DB was 'open', Tradier is '{tradier_status}'"
                )
                continue

            # Check for OCO type mismatches: DB says OCO but Tradier has plain equity order
            db_type = db_order["order_type"]
            if db_type in ("t1_oco", "t2_oco", "t3_oco"):
                tradier_class = tradier_classes.get(order_id, "")
                if tradier_class == "equity":
                    # DB thinks this is an OCO but Tradier has a plain stop/limit
                    # Cancel the wrong order and mark as cancelled so enforce can re-inject
                    try:
                        client.cancel_order(int(order_id))
                    except Exception:
                        pass
                    conn.execute(
                        "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
                        (now, db_order["id"]),
                    )
                    logger.warning(
                        f"Order {order_id} (trade {db_order['trade_id']}) type mismatch: "
                        f"DB says '{db_type}' but Tradier class is '{tradier_class}' — cancelled for re-injection"
                    )


def _process_state_flags(client):
    """Process state transitions: inject brackets, cascade T2/T3.
    Uses shared throttle to prevent retry loops when orders keep getting rejected."""
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        # ENTRY_FILLED -> inject exit orders (T1 OCO + remaining stop)
        entry_filled = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND position_state = 'ENTRY_FILLED'"
        ).fetchall()

        for trade in entry_filled:
            if _is_injection_throttled(trade["id"]):
                continue
            success = _inject_exit_orders(client, conn, trade, now)
            if success:
                _clear_injection_failures(trade["id"])
            else:
                _record_injection_failure(trade["id"])

        # T1_FILLED -> cascade T2 OCO + T3 stop
        t1_filled = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND position_state = 'T1_FILLED' "
            "AND t1_filled = 1 AND t2_filled = 0 AND shares_remaining > 0"
        ).fetchall()

        for trade in t1_filled:
            if _is_injection_throttled(trade["id"]):
                continue
            success = _cascade_t2_t3(client, conn, trade, now)
            if success:
                _clear_injection_failures(trade["id"])
            else:
                _record_injection_failure(trade["id"])

        # T2_FILLED -> T3 bracket
        t2_filled = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND position_state = 'T2_FILLED' "
            "AND t2_filled = 1 AND t3_filled = 0 AND shares_remaining > 0"
        ).fetchall()

        for trade in t2_filled:
            if _is_injection_throttled(trade["id"]):
                continue
            success = _place_t3_bracket(client, conn, trade, now)
            if success:
                _clear_injection_failures(trade["id"])
            else:
                _record_injection_failure(trade["id"])


def _inject_exit_orders(client, conn, trade, now) -> bool:
    """Place T1 OCO + remaining stop after entry fill. Returns True on success."""
    symbol = trade["symbol"]
    t1_shares = trade["t1_shares"]
    remaining_shares = trade["shares_remaining"] - t1_shares

    stop_cents = _cap_stop_price(symbol, trade["stop_price_cents"])
    t1_price = trade["target_t1_price_cents"] / 100
    stop_price = stop_cents / 100

    try:
        if t1_shares > 0:
            # T1 OCO: limit at T1 + stop at stop_loss
            result = client.place_oco_order(symbol, t1_shares, t1_price, stop_price)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 't1_oco', ?, ?, 'open', ?, ?)",
                (trade["id"], order_id, t1_shares, trade["target_t1_price_cents"], now, now),
            )

        if remaining_shares > 0:
            # Remaining stop
            result = client.place_stop_order(symbol, "sell", remaining_shares, stop_price)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 'stop', ?, ?, 'open', ?, ?)",
                (trade["id"], order_id, remaining_shares, trade["stop_price_cents"], now, now),
            )

        conn.execute(
            "UPDATE trades SET position_state = 'BRACKET_PLACED', updated_at = ? WHERE id = ?",
            (now, trade["id"]),
        )
        logger.info(f"Brackets placed: {symbol} T1 OCO ({t1_shares}sh) + stop ({remaining_shares}sh)")
        return True
    except Exception as e:
        logger.error(f"Failed to inject exits for {symbol}: {e}")
        return False


def _cascade_t2_t3(client, conn, trade, now) -> bool:
    """After T1 fill: cancel old stops, place T2 OCO + T3 stop at breakeven. Returns True on success."""
    symbol = trade["symbol"]
    t2_shares = trade["t2_shares"]
    t3_shares = trade["t3_shares"]
    stop_cents = _cap_stop_price(symbol, trade["stop_price_cents"])

    # Cancel existing stop orders (mark DB cancelled even if Tradier cancel fails)
    orders = conn.execute(
        "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open' AND order_type = 'stop'",
        (trade["id"],),
    ).fetchall()
    for order in orders:
        try:
            client.cancel_order(int(order["order_id"]))
            client.wait_for_cancel(int(order["order_id"]))
        except Exception as e:
            logger.warning(f"Cancel order {order['order_id']}: {e}")
        conn.execute("UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?", (now, order["id"]))

    try:
        if t2_shares > 0:
            t2_price = trade["target_t2_price_cents"] / 100
            stop_price = stop_cents / 100
            result = client.place_oco_order(symbol, t2_shares, t2_price, stop_price)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 't2_oco', ?, ?, 'open', ?, ?)",
                (trade["id"], order_id, t2_shares, trade["target_t2_price_cents"], now, now),
            )

        if t3_shares > 0:
            stop_price = stop_cents / 100
            result = client.place_stop_order(symbol, "sell", t3_shares, stop_price)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 'stop', ?, ?, 'open', ?, ?)",
                (trade["id"], order_id, t3_shares, stop_cents, now, now),
            )

        logger.info(f"T2/T3 cascade: {symbol} T2 OCO ({t2_shares}sh) + T3 stop ({t3_shares}sh)")
        return True
    except Exception as e:
        logger.error(f"Failed T2/T3 cascade for {symbol}: {e}")
        return False


def _place_t3_bracket(client, conn, trade, now) -> bool:
    """After T2 fill: place T3 OCO at breakeven. Returns True on success."""
    symbol = trade["symbol"]
    t3_shares = trade["shares_remaining"]
    if t3_shares <= 0:
        return True

    # Cancel old stops (mark DB cancelled even if Tradier cancel fails)
    orders = conn.execute(
        "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open' AND order_type = 'stop'",
        (trade["id"],),
    ).fetchall()
    for order in orders:
        try:
            client.cancel_order(int(order["order_id"]))
        except Exception as e:
            logger.warning(f"Cancel order {order['order_id']} for {symbol}: {e}")
        conn.execute("UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?", (now, order["id"]))

    stop_cents = _cap_stop_price(symbol, trade["stop_price_cents"])

    try:
        t3_price = trade["target_t3_price_cents"] / 100
        stop_price = stop_cents / 100
        result = client.place_oco_order(symbol, t3_shares, t3_price, stop_price)
        order_id = str(result.get("id", ""))
        conn.execute(
            "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
            "VALUES (?, ?, 't3_oco', ?, ?, 'open', ?, ?)",
            (trade["id"], order_id, t3_shares, trade["target_t3_price_cents"], now, now),
        )
        logger.info(f"T3 bracket: {symbol} OCO ({t3_shares}sh)")
        return True
    except Exception as e:
        logger.error(f"Failed T3 bracket for {symbol}: {e}")
        return False


def _apply_stepped_stops(client):
    """Ratchet stepped stops UP-only. Handles manual trade ratchets separately."""
    params = get_trading_params()
    store = DataStore()
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db() as conn:
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND position_state IN ('BRACKET_PLACED', 'T1_FILLED', 'T2_FILLED')"
        ).fetchall()

        # Sort by trade_id ascending (FLAT-C4 deadlock prevention)
        open_trades = sorted(open_trades, key=lambda t: t["id"])

        for trade in open_trades:
            # Manual trade handling
            trade_type = trade["trade_type"] if "trade_type" in trade.keys() else "auto"
            if trade_type == "manual":
                ratchet_enabled = trade["ratchet_enabled"] if "ratchet_enabled" in trade.keys() else 0
                if ratchet_enabled:
                    from tradier.manual_trades import get_trade_lock
                    lock = get_trade_lock(trade["id"])
                    with lock:
                        _calculate_and_apply_manual_ratchet(client, conn, trade, today, now)
                continue  # Skip auto trade stepped stop logic for ALL manual trades

            if not params["stepped_stops"]["enabled"]:
                continue

            # Check if already ratcheted today
            if trade["last_stepped_stop_date"] == today:
                continue

            entry_time = datetime.fromisoformat(trade["entry_time"])
            active_days = (datetime.now(timezone.utc) - entry_time).days

            # Get current ATR (dynamic, Review Finding: use_dynamic_atr)
            current_atr = store.get_current_atr(trade["symbol"])
            if current_atr is None:
                continue

            step_size = params["stepped_stops"]["step_size"]
            base_stop_cents = trade["base_stop_cents"]
            current_stop_cents = trade["stop_price_cents"]

            # Formula: stepped_stop = base_stop + (active_days * step_size * ATR)
            stepped_stop = base_stop_cents / 100 + (active_days * step_size * current_atr)
            stepped_stop_cents = round(stepped_stop * 100)

            # Only ratchet UP
            new_stop_cents = max(current_stop_cents, stepped_stop_cents)

            # Cascade guard: don't ratchet above breakeven level before T1 fills
            # After T1, base_stop is already set to breakeven — this prevents
            # stepped stops from jumping above where breakeven would place them
            if trade["position_state"] == "BRACKET_PLACED":
                entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
                be_offset = params["breakeven"]["offset_pct"] / 100
                breakeven_cap = round(entry_cents * (1 + be_offset))
                new_stop_cents = min(new_stop_cents, breakeven_cap)

            # Cap at current_price * 0.995
            new_stop_cents = _cap_stop_price(trade["symbol"], new_stop_cents)

            if new_stop_cents > current_stop_cents:
                # Update stop in DB
                conn.execute(
                    "UPDATE trades SET stop_price_cents = ?, last_stepped_stop_date = ?, updated_at = ? WHERE id = ?",
                    (new_stop_cents, today, now, trade["id"]),
                )

                # Update stop orders on Tradier via cancel-and-replace
                # (modify_order can silently fail on OCO legs)
                orders = conn.execute(
                    "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open' AND order_type IN ('stop', 't1_oco', 't2_oco', 't3_oco')",
                    (trade["id"],),
                ).fetchall()

                for order in orders:
                    try:
                        client.cancel_order(int(order["order_id"]))
                        client.wait_for_cancel(int(order["order_id"]))
                    except Exception as e:
                        logger.warning(f"Failed to cancel order {order['order_id']} for {trade['symbol']}: {e}")
                    # Mark cancelled in DB even if Tradier cancel failed (prevents stale retries)
                    conn.execute(
                        "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
                        (now, order["id"]),
                    )

                    try:
                        qty = order["shares"] if "shares" in order.keys() else trade["shares_remaining"]
                        order_type = order["order_type"]

                        # Preserve OCO structure: re-place as OCO if it was an OCO
                        if order_type in ("t1_oco", "t2_oco", "t3_oco"):
                            target_key = {"t1_oco": "target_t1_price_cents", "t2_oco": "target_t2_price_cents", "t3_oco": "target_t3_price_cents"}[order_type]
                            target_price = trade[target_key] / 100
                            new_order = client.place_oco_order(
                                symbol=trade["symbol"],
                                quantity=qty,
                                limit_price=target_price,
                                stop_price=new_stop_cents / 100,
                            )
                        else:
                            new_order = client.place_stop_order(
                                symbol=trade["symbol"],
                                side="sell",
                                quantity=qty,
                                stop_price=new_stop_cents / 100,
                            )
                        new_order_id = str(new_order.get("id", ""))
                        if new_order_id:
                            conn.execute(
                                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                                "VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                                (trade["id"], new_order_id, order_type, qty, new_stop_cents, now, now),
                            )
                            logger.info(f"Replaced {order_type} order {order['order_id']} -> {new_order_id} for {trade['symbol']}")
                        else:
                            logger.warning(f"Order replacement for {trade['symbol']} returned no order ID")
                    except Exception as e:
                        logger.error(
                            f"Replacement order failed for {trade['symbol']} after cancel: {e} — "
                            f"position may be unprotected, will retry next cycle"
                        )

                logger.info(
                    f"Stepped stop: {trade['symbol']} ${current_stop_cents/100:.2f} -> ${new_stop_cents/100:.2f} "
                    f"(day {active_days}, ATR ${current_atr:.2f})"
                )


def _calculate_and_apply_manual_ratchet(client, conn, trade, today, now):
    """Apply ratchet trailing stop for a manual trade."""
    from core.manual_price_calc import calculate_ratchet_stop

    symbol = trade["symbol"]
    trade_id = trade["id"]

    if trade["last_stepped_stop_date"] == today:
        return

    store = DataStore()
    current_atr = store.get_current_atr(symbol)
    if current_atr is None or current_atr <= 0:
        return

    # Get current price for ratchet high
    latest = store.get_latest_indicators(symbol)
    if not latest:
        return

    current_price_cents = round(latest["close"] * 100)
    ratchet_high = trade["ratchet_high_cents"] or trade["entry_price_cents"]
    new_high = max(ratchet_high, current_price_cents)

    ratchet_mode = trade["ratchet_mode"]
    ratchet_value = trade["ratchet_value"]
    if not ratchet_mode or not ratchet_value:
        return

    current_stop = trade["stop_price_cents"]
    new_stop = calculate_ratchet_stop(new_high, ratchet_mode, ratchet_value, current_atr, current_stop)

    # Cap at current_price * 0.995
    new_stop = _cap_stop_price(symbol, new_stop)

    # Update ratchet high always (even if stop didn't move)
    if new_high > ratchet_high:
        conn.execute(
            "UPDATE trades SET ratchet_high_cents = ?, updated_at = ? WHERE id = ?",
            (new_high, now, trade_id),
        )

    if new_stop > current_stop:
        conn.execute(
            "UPDATE trades SET stop_price_cents = ?, last_stepped_stop_date = ?, updated_at = ? WHERE id = ?",
            (new_stop, today, now, trade_id),
        )

        # Cancel-and-replace stop orders
        orders = conn.execute(
            "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open' AND order_type IN ('stop', 't1_oco', 't2_oco', 't3_oco')",
            (trade_id,),
        ).fetchall()

        for order in orders:
            try:
                client.cancel_order(int(order["order_id"]))
                client.wait_for_cancel(int(order["order_id"]))
            except Exception as e:
                logger.warning(f"Failed to cancel order {order['order_id']} for ratchet {symbol}: {e}")
            # Mark cancelled in DB even if Tradier cancel failed
            conn.execute(
                "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, order["id"]),
            )

            try:
                order_type = order["order_type"]
                qty = order["shares"] if "shares" in order.keys() else trade["shares_remaining"]

                # Preserve OCO structure: re-place as OCO if it was an OCO
                if order_type in ("t1_oco", "t2_oco", "t3_oco"):
                    target_key = {"t1_oco": "target_t1_price_cents", "t2_oco": "target_t2_price_cents", "t3_oco": "target_t3_price_cents"}[order_type]
                    target_price = trade[target_key] / 100
                    new_order = client.place_oco_order(symbol, qty, target_price, new_stop / 100)
                else:
                    new_order = client.place_stop_order(symbol, "sell", qty, new_stop / 100)

                new_order_id = str(new_order.get("id", ""))
                if new_order_id:
                    conn.execute(
                        "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                        (trade_id, new_order_id, order_type, qty, new_stop, now, now),
                    )
            except Exception as e:
                logger.error(f"Ratchet replacement failed for {symbol}: {e} — will retry next cycle")

        logger.info(f"Manual ratchet: {symbol} ${current_stop/100:.2f} -> ${new_stop/100:.2f}")


def _inject_stop_only(client, conn, trade, now):
    """Place a single stop order for hold-mode manual trades."""
    symbol = trade["symbol"]
    shares = trade["shares_remaining"]
    stop_cents = _cap_stop_price(symbol, trade["stop_price_cents"])
    stop_price = stop_cents / 100

    try:
        result = client.place_stop_order(symbol, "sell", shares, stop_price)
        order_id = str(result.get("id", ""))
        conn.execute(
            "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
            "VALUES (?, ?, 'stop', ?, ?, 'open', ?, ?)",
            (trade["id"], order_id, shares, trade["stop_price_cents"], now, now),
        )
        conn.execute(
            "UPDATE trades SET position_state = 'BRACKET_PLACED', updated_at = ? WHERE id = ?",
            (now, trade["id"]),
        )
        logger.info(f"Stop-only injected for hold-mode trade: {symbol}")
    except Exception as e:
        logger.error(f"Failed to inject stop-only for {symbol}: {e}")


def _is_injection_throttled(trade_id: int) -> bool:
    """Check if re-injection for this trade should be throttled."""
    entry = _injection_failures.get(trade_id)
    if not entry:
        return False
    fail_count, last_fail = entry
    if fail_count >= _INJECTION_MAX_RETRIES:
        elapsed = (datetime.now(timezone.utc) - last_fail).total_seconds() / 60
        if elapsed < _INJECTION_COOLDOWN_MINUTES:
            return True
        # Cooldown expired, reset and allow retry
        _injection_failures.pop(trade_id, None)
    return False


def _record_injection_failure(trade_id: int):
    """Record a failed injection attempt for throttling."""
    entry = _injection_failures.get(trade_id)
    if entry:
        _injection_failures[trade_id] = (entry[0] + 1, datetime.now(timezone.utc))
    else:
        _injection_failures[trade_id] = (1, datetime.now(timezone.utc))


def _clear_injection_failures(trade_id: int):
    """Clear throttle on successful injection."""
    _injection_failures.pop(trade_id, None)


def _verify_new_orders(client, conn, trade_id: int, created_at: str) -> bool:
    """Check if recently placed orders were async-rejected by Tradier.
    Tradier accepts the HTTP request (returns an order ID) but may reject
    asynchronously if ghost orders consume sell capacity. Returns True
    if all orders are still live after a brief wait."""
    time.sleep(1.5)
    orders = conn.execute(
        "SELECT order_id FROM order_state WHERE trade_id = ? AND status = 'open' AND created_at = ?",
        (trade_id, created_at),
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    all_ok = True
    for order in orders:
        try:
            tradier_order = client.get_order(int(order["order_id"]))
            status = tradier_order.get("status", "")
            if status in ("rejected", "expired", "canceled"):
                reason = tradier_order.get("reason_description", "unknown")
                logger.warning(
                    f"Order {order['order_id']} async-rejected by Tradier: {reason}"
                )
                conn.execute(
                    "UPDATE order_state SET status = ?, updated_at = ? "
                    "WHERE order_id = ? AND trade_id = ?",
                    (status, now, order["order_id"], trade_id),
                )
                all_ok = False
        except Exception:
            pass
    return all_ok


def _cancel_ghost_orders(client, conn, symbol: str, db_open_order_ids: set):
    """Cancel Tradier open sell orders for a symbol that aren't tracked in the DB.
    These 'ghost' orders can occur when a cancel-and-replace fails (e.g. 401)
    and the DB marks the order as cancelled but Tradier still has it open.
    If not cleaned up, they consume share capacity and cause new orders to be
    rejected with 'more shares than your current long position'."""
    try:
        all_orders = client.get_orders()
    except Exception as e:
        logger.error(f"Failed to fetch orders for ghost cleanup of {symbol}: {e}")
        return 0

    cancelled = 0
    for order in all_orders:
        # Check top-level equity orders
        if (order.get("symbol") == symbol
                and order.get("side") == "sell"
                and order.get("status") in ("open", "pending")
                and str(order["id"]) not in db_open_order_ids):
            try:
                client.cancel_order(order["id"])
                cancelled += 1
                logger.info(f"Cancelled ghost order {order['id']} for {symbol} "
                            f"(type={order.get('type')} qty={order.get('quantity')})")
            except Exception as e:
                logger.warning(f"Failed to cancel ghost order {order['id']} for {symbol}: {e}")

        # Check OCO legs
        legs = order.get("leg", [])
        if isinstance(legs, dict):
            legs = [legs]
        has_symbol_leg = any(
            isinstance(l, dict) and l.get("symbol") == symbol and l.get("side") == "sell"
            for l in legs
        )
        if (has_symbol_leg
                and order.get("status") in ("open", "pending")
                and str(order["id"]) not in db_open_order_ids
                and not any(str(l.get("id", "")) in db_open_order_ids for l in legs if isinstance(l, dict))):
            try:
                client.cancel_order(order["id"])
                cancelled += 1
                logger.info(f"Cancelled ghost OCO {order['id']} for {symbol}")
            except Exception as e:
                logger.warning(f"Failed to cancel ghost OCO {order['id']} for {symbol}: {e}")

    if cancelled:
        logger.warning(f"Cleaned up {cancelled} ghost order(s) for {symbol} before re-injection")
    return cancelled


def _enforce_order_structure(client):
    """Verify all open positions have proper protection orders (including OCO where expected)."""
    with get_db() as conn:
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND position_state NOT IN ('ENTRY_PENDING', 'CLOSED', 'ADOPTING', 'FLATTEN_PENDING')"
        ).fetchall()

        # Prune throttle entries for closed/missing trades
        open_trade_ids = {t["id"] for t in open_trades}
        stale_ids = [tid for tid in _injection_failures if tid not in open_trade_ids]
        for tid in stale_ids:
            _injection_failures.pop(tid, None)

        for trade in open_trades:
            if trade["shares_remaining"] <= 0:
                continue

            trade_type = trade["trade_type"] if "trade_type" in trade.keys() else "auto"
            targets_enabled = True
            if trade_type == "manual":
                targets_enabled = bool(trade["targets_enabled"]) if "targets_enabled" in trade.keys() else True

            open_orders = conn.execute(
                "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open'",
                (trade["id"],),
            ).fetchall()

            open_count = len(open_orders)
            has_oco = any(o["order_type"] in ("t1_oco", "t2_oco", "t3_oco") for o in open_orders)

            # Determine if re-injection is needed
            needs_reinject = False
            if open_count == 0:
                logger.warning(f"No protection orders for {trade['symbol']} ({trade['shares_remaining']}sh) — re-injecting")
                needs_reinject = True
            elif targets_enabled and not has_oco and trade["position_state"] in ("BRACKET_PLACED", "ENTRY_FILLED"):
                # Has orders but missing OCO — structure was corrupted
                logger.warning(
                    f"Missing OCO for {trade['symbol']} ({trade['shares_remaining']}sh, "
                    f"{open_count} open orders but no OCO) — cancelling and re-injecting"
                )
                for order in open_orders:
                    try:
                        client.cancel_order(int(order["order_id"]))
                    except Exception:
                        pass
                    conn.execute(
                        "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(), order["id"]),
                    )
                needs_reinject = True
            elif open_count > 0:
                # Check total coverage: sum of open order shares must cover shares_remaining
                covered_shares = sum(o["shares"] for o in open_orders)
                if covered_shares < trade["shares_remaining"]:
                    logger.warning(
                        f"Incomplete protection for {trade['symbol']}: {covered_shares}sh covered "
                        f"of {trade['shares_remaining']}sh remaining — cancelling and re-injecting"
                    )
                    for order in open_orders:
                        try:
                            client.cancel_order(int(order["order_id"]))
                        except Exception:
                            pass
                        conn.execute(
                            "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
                            (datetime.now(timezone.utc).isoformat(), order["id"]),
                        )
                    needs_reinject = True

            if not needs_reinject:
                # Successful state — clear any throttle
                _clear_injection_failures(trade["id"])
                continue

            # ALWAYS run ghost cleanup before throttle check.
            # Ghost GTC orders at Tradier (from failed cancel-and-replace) consume
            # sell capacity and cause new orders to be async-rejected.
            # Must run even when throttled so ghosts get cleaned up during cooldown.
            db_open_ids = {str(o["order_id"]) for o in open_orders}
            _cancel_ghost_orders(client, conn, trade["symbol"], db_open_ids)

            # Check throttle before re-injecting
            if _is_injection_throttled(trade["id"]):
                entry = _injection_failures.get(trade["id"])
                logger.warning(
                    f"Re-injection throttled for {trade['symbol']} (trade {trade['id']}): "
                    f"{entry[0]} failures, cooldown until {entry[1].isoformat()} + {_INJECTION_COOLDOWN_MINUTES}min"
                )
                continue

            now = datetime.now(timezone.utc).isoformat()

            # Manual hold-mode: stop-only
            if trade_type == "manual" and not targets_enabled:
                _inject_stop_only(client, conn, trade, now)
                continue

            success = False
            if trade["position_state"] in ("ENTRY_FILLED", "BRACKET_PLACED"):
                success = _inject_exit_orders(client, conn, trade, now)
            elif trade["position_state"] == "T1_FILLED":
                success = _cascade_t2_t3(client, conn, trade, now)
            elif trade["position_state"] == "T2_FILLED":
                success = _place_t3_bracket(client, conn, trade, now)

            if success:
                # Verify orders weren't async-rejected by Tradier
                # (Tradier accepts HTTP request but rejects if ghost orders
                # or other issues consume sell capacity)
                success = _verify_new_orders(client, conn, trade["id"], now)

            if success:
                _clear_injection_failures(trade["id"])
            else:
                _record_injection_failure(trade["id"])
