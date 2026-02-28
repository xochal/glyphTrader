"""
Manual trade management: adoption, stop/target updates, hold mode, release.
Per-trade locking for cancel-and-replace atomicity.
"""

import threading
import logging
from datetime import datetime, timezone
from typing import Dict

from core.manual_price_calc import (
    calculate_stop_price,
    calculate_target_price,
    validate_target_ordering,
)
from data.datastore import DataStore
from db.database import get_db
from db import crypto

logger = logging.getLogger("glyphTrader.manual_trades")

# Per-trade locks (ARCH-1)
_trade_locks: Dict[int, threading.Lock] = {}
_trade_locks_guard = threading.Lock()


def get_trade_lock(trade_id: int) -> threading.Lock:
    """Get or create a lock for a specific trade."""
    with _trade_locks_guard:
        if trade_id not in _trade_locks:
            _trade_locks[trade_id] = threading.Lock()
        return _trade_locks[trade_id]


def _get_client():
    """Get Tradier client."""
    from tradier.execution import _get_tradier_client
    return _get_tradier_client()


def _get_atr(symbol: str) -> float:
    """Get current ATR for a symbol from DataStore."""
    store = DataStore()
    atr = store.get_current_atr(symbol)
    if atr is None or atr <= 0:
        raise ValueError(f"ATR unavailable or invalid for {symbol}")
    return atr


def _calculate_share_distribution(total_shares: int, t1_pct: int, t2_pct: int, t3_pct: int) -> dict:
    """Distribute shares across targets by percentage."""
    t1_shares = max(1, round(total_shares * t1_pct / 100))
    t2_shares = max(1, round(total_shares * t2_pct / 100)) if total_shares > 1 else 0
    t3_shares = total_shares - t1_shares - t2_shares
    if t3_shares < 0:
        t2_shares += t3_shares
        t3_shares = 0
    return {"t1_shares": t1_shares, "t2_shares": t2_shares, "t3_shares": t3_shares}


def adopt_position(symbol: str, shares: int, entry_price_cents: int, config: dict) -> dict:
    """
    Adopt an orphan position: create DB record, place protective orders.
    Returns dict with trade_id on success, or error on failure.
    """
    if not crypto.is_unlocked():
        return {"error": "System is locked", "status_code": 423}

    # Fetch data if not in DataStore
    store = DataStore()
    if store.get_latest_indicators(symbol) is None:
        try:
            from data.market_data import fetch_single_symbol
            client = _get_client()
            result_df = fetch_single_symbol(client, symbol)
            if result_df is not None and not result_df.empty:
                # Enrich and store in DataStore so ATR is available
                from core.indicators import calculate_all_indicators
                enriched = calculate_all_indicators(result_df)
                store.update_symbol(symbol, enriched)
                logger.info(f"Fetched and enriched {symbol} for adoption ({len(result_df)} bars)")
            else:
                logger.warning(f"No candle data returned for {symbol}")
        except Exception as e:
            logger.warning(f"Could not fetch data for {symbol}: {e}")

    # Get ATR
    try:
        atr = _get_atr(symbol)
    except ValueError as e:
        return {"error": str(e)}

    # Calculate prices
    try:
        stop_cents = calculate_stop_price(entry_price_cents, config["stop_mode"], config["stop_value"], atr)

        t1_cents = t2_cents = t3_cents = 0
        if config["targets_enabled"]:
            t1_cents = calculate_target_price(entry_price_cents, config["t1_mode"], config["t1_value"], atr)
            t2_cents = calculate_target_price(entry_price_cents, config["t2_mode"], config["t2_value"], atr)
            t3_cents = calculate_target_price(entry_price_cents, config["t3_mode"], config["t3_value"], atr)
            validate_target_ordering(t1_cents, t2_cents, t3_cents, entry_price_cents)
    except ValueError as e:
        return {"error": str(e)}

    # Share distribution
    if config["targets_enabled"]:
        dist = _calculate_share_distribution(
            shares, config["t1_exit_pct"], config["t2_exit_pct"], config["t3_exit_pct"]
        )
    else:
        dist = {"t1_shares": 0, "t2_shares": 0, "t3_shares": shares}

    now = datetime.now(timezone.utc).isoformat()
    atr_cents = round(atr * 100)

    # INSERT trade in ADOPTING state
    with get_db() as conn:
        conn.execute(
            "INSERT INTO trades (symbol, entry_price_cents, entry_time, shares, shares_remaining, "
            "stop_price_cents, base_stop_cents, target_t1_price_cents, target_t2_price_cents, "
            "target_t3_price_cents, original_atr_cents, position_state, "
            "t1_shares, t2_shares, t3_shares, blended_entry_price_cents, "
            "trade_type, stop_mode, stop_mode_value, "
            "ratchet_enabled, ratchet_mode, ratchet_value, ratchet_high_cents, "
            "t1_mode, t1_mode_value, t2_mode, t2_mode_value, t3_mode, t3_mode_value, "
            "targets_enabled, t1_exit_pct, t2_exit_pct, t3_exit_pct, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ADOPTING', "
            "?, ?, ?, ?, "
            "'manual', ?, ?, "
            "?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, "
            "?, ?)",
            (symbol, entry_price_cents, now, shares, shares,
             stop_cents, stop_cents, t1_cents, t2_cents, t3_cents,
             atr_cents,
             dist["t1_shares"], dist["t2_shares"], dist["t3_shares"],
             entry_price_cents,
             config["stop_mode"], config["stop_value"],
             1 if config["ratchet_enabled"] else 0,
             config.get("ratchet_mode"), config.get("ratchet_value"),
             entry_price_cents,  # ratchet_high starts at entry
             config["t1_mode"] if config["targets_enabled"] else None,
             config["t1_value"] if config["targets_enabled"] else None,
             config["t2_mode"] if config["targets_enabled"] else None,
             config["t2_value"] if config["targets_enabled"] else None,
             config["t3_mode"] if config["targets_enabled"] else None,
             config["t3_value"] if config["targets_enabled"] else None,
             1 if config["targets_enabled"] else 0,
             config["t1_exit_pct"] if config["targets_enabled"] else None,
             config["t2_exit_pct"] if config["targets_enabled"] else None,
             config["t3_exit_pct"] if config["targets_enabled"] else None,
             now, now),
        )
        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Place orders on Tradier
    try:
        client = _get_client()
        _place_manual_orders(client, trade_id, symbol, shares, stop_cents, t1_cents, t2_cents, t3_cents, dist, config["targets_enabled"])

        # Transition to BRACKET_PLACED
        with get_db() as conn:
            conn.execute(
                "UPDATE trades SET position_state = 'BRACKET_PLACED', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), trade_id),
            )

        logger.info(f"Adopted {symbol}: {shares}sh, stop ${stop_cents/100:.2f}, trade_id={trade_id}")
        return {"trade_id": trade_id, "symbol": symbol, "state": "BRACKET_PLACED"}

    except Exception as e:
        logger.error(f"Order placement failed for adoption of {symbol}: {e}")
        return {"trade_id": trade_id, "error": f"Orders failed: {e}. Trade is in ADOPTING state — retry or delete."}


def _place_manual_orders(client, trade_id, symbol, shares, stop_cents, t1_cents, t2_cents, t3_cents, dist, targets_enabled):
    """Place stop/OCO orders for a manual trade."""
    now = datetime.now(timezone.utc).isoformat()
    stop_price = stop_cents / 100

    with get_db() as conn:
        if targets_enabled and dist["t1_shares"] > 0:
            # T1 OCO
            result = client.place_oco_order(symbol, dist["t1_shares"], t1_cents / 100, stop_price)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 't1_oco', ?, ?, 'open', ?, ?)",
                (trade_id, order_id, dist["t1_shares"], t1_cents, now, now),
            )

            # Remaining stop for T2+T3 shares
            remaining = dist["t2_shares"] + dist["t3_shares"]
            if remaining > 0:
                result = client.place_stop_order(symbol, "sell", remaining, stop_price)
                order_id = str(result.get("id", ""))
                conn.execute(
                    "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                    "VALUES (?, ?, 'stop', ?, ?, 'open', ?, ?)",
                    (trade_id, order_id, remaining, stop_cents, now, now),
                )
        else:
            # Hold mode or no targets: stop only for all shares
            result = client.place_stop_order(symbol, "sell", shares, stop_price)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 'stop', ?, ?, 'open', ?, ?)",
                (trade_id, order_id, shares, stop_cents, now, now),
            )


def delete_adopting_trade(trade_id: int) -> dict:
    """Delete a stuck ADOPTING trade and its orders."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute("DELETE FROM order_state WHERE trade_id = ?", (trade_id,))
        conn.execute("DELETE FROM trades WHERE id = ? AND position_state = 'ADOPTING'", (trade_id,))
    logger.info(f"Deleted ADOPTING trade {trade_id}")
    return {"deleted": True, "trade_id": trade_id}


def retry_adoption_orders(trade_id: int) -> dict:
    """Re-attempt order placement for stuck ADOPTING trades."""
    with get_db() as conn:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade or trade["position_state"] != "ADOPTING":
            return {"error": "Trade not found or not in ADOPTING state"}

    symbol = trade["symbol"]
    shares = trade["shares_remaining"]
    stop_cents = trade["stop_price_cents"]
    t1_cents = trade["target_t1_price_cents"]
    t2_cents = trade["target_t2_price_cents"]
    t3_cents = trade["target_t3_price_cents"]
    targets_enabled = bool(trade["targets_enabled"])

    dist = {
        "t1_shares": trade["t1_shares"],
        "t2_shares": trade["t2_shares"],
        "t3_shares": trade["t3_shares"],
    }

    try:
        client = _get_client()
        # Cancel any partial orders from previous attempt
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            old_orders = conn.execute(
                "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open'", (trade_id,)
            ).fetchall()
            for o in old_orders:
                try:
                    client.cancel_order(int(o["order_id"]))
                except Exception:
                    pass
                conn.execute("UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?", (now, o["id"]))

        _place_manual_orders(client, trade_id, symbol, shares, stop_cents, t1_cents, t2_cents, t3_cents, dist, targets_enabled)

        with get_db() as conn:
            conn.execute(
                "UPDATE trades SET position_state = 'BRACKET_PLACED', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), trade_id),
            )

        return {"trade_id": trade_id, "state": "BRACKET_PLACED"}
    except Exception as e:
        logger.error(f"Retry failed for trade {trade_id}: {e}")
        return {"error": str(e)}


def update_trade_stops(trade_id: int, config: dict) -> dict:
    """Update stop and ratchet settings for a manual trade."""
    lock = get_trade_lock(trade_id)
    with lock:
        with get_db() as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND status = 'open'",
                (trade_id,),
            ).fetchone()
            if not trade:
                return {"error": "Trade not found"}

            symbol = trade["symbol"]
            entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]

            try:
                atr = _get_atr(symbol)
                new_stop = calculate_stop_price(entry_cents, config["stop_mode"], config["stop_value"], atr)
            except ValueError as e:
                return {"error": str(e)}

            now = datetime.now(timezone.utc).isoformat()

            # Update DB
            conn.execute(
                "UPDATE trades SET stop_price_cents = ?, base_stop_cents = ?, "
                "stop_mode = ?, stop_mode_value = ?, "
                "ratchet_enabled = ?, ratchet_mode = ?, ratchet_value = ?, "
                "updated_at = ? WHERE id = ?",
                (new_stop, new_stop,
                 config["stop_mode"], config["stop_value"],
                 1 if config.get("ratchet_enabled") else 0,
                 config.get("ratchet_mode"), config.get("ratchet_value"),
                 now, trade_id),
            )

            # Cancel-and-replace stop orders on Tradier
            try:
                client = _get_client()
                _cancel_and_replace_stops(client, conn, trade_id, symbol, new_stop, trade["shares_remaining"], now)
            except Exception as e:
                logger.error(f"Failed to replace stops on Tradier for trade {trade_id}: {e}")
                return {"error": f"DB updated but Tradier order update failed: {e}"}

    return {"trade_id": trade_id, "stop_price_cents": new_stop}


def update_trade_targets(trade_id: int, config: dict) -> dict:
    """Update target prices and exit percentages for a manual trade."""
    lock = get_trade_lock(trade_id)
    with lock:
        with get_db() as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND status = 'open'",
                (trade_id,),
            ).fetchone()
            if not trade:
                return {"error": "Trade not found"}

            if not trade["targets_enabled"]:
                return {"error": "Cannot update targets while hold mode is enabled"}

            symbol = trade["symbol"]
            entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]

            try:
                atr = _get_atr(symbol)
                t1 = calculate_target_price(entry_cents, config["t1_mode"], config["t1_value"], atr)
                t2 = calculate_target_price(entry_cents, config["t2_mode"], config["t2_value"], atr)
                t3 = calculate_target_price(entry_cents, config["t3_mode"], config["t3_value"], atr)
                validate_target_ordering(t1, t2, t3, entry_cents)
            except ValueError as e:
                return {"error": str(e)}

            shares = trade["shares_remaining"]
            dist = _calculate_share_distribution(shares, config["t1_exit_pct"], config["t2_exit_pct"], config["t3_exit_pct"])
            now = datetime.now(timezone.utc).isoformat()
            stop_cents = trade["stop_price_cents"]

            conn.execute(
                "UPDATE trades SET target_t1_price_cents = ?, target_t2_price_cents = ?, "
                "target_t3_price_cents = ?, "
                "t1_mode = ?, t1_mode_value = ?, t2_mode = ?, t2_mode_value = ?, "
                "t3_mode = ?, t3_mode_value = ?, "
                "t1_exit_pct = ?, t2_exit_pct = ?, t3_exit_pct = ?, "
                "t1_shares = ?, t2_shares = ?, t3_shares = ?, "
                "updated_at = ? WHERE id = ?",
                (t1, t2, t3,
                 config["t1_mode"], config["t1_value"],
                 config["t2_mode"], config["t2_value"],
                 config["t3_mode"], config["t3_value"],
                 config["t1_exit_pct"], config["t2_exit_pct"], config["t3_exit_pct"],
                 dist["t1_shares"], dist["t2_shares"], dist["t3_shares"],
                 now, trade_id),
            )

            # Cancel all existing orders and re-place
            try:
                client = _get_client()
                _cancel_all_orders(client, conn, trade_id, now)
                _place_manual_orders(client, trade_id, symbol, shares, stop_cents, t1, t2, t3, dist, True)
            except Exception as e:
                logger.error(f"Failed to replace orders for trade {trade_id}: {e}")
                return {"error": f"DB updated but Tradier order update failed: {e}"}

    return {"trade_id": trade_id, "t1": t1, "t2": t2, "t3": t3}


def set_hold_mode(trade_id: int, enabled: bool) -> dict:
    """Toggle hold mode: cancel targets or restore them."""
    lock = get_trade_lock(trade_id)
    with lock:
        with get_db() as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND status = 'open'",
                (trade_id,),
            ).fetchone()
            if not trade:
                return {"error": "Trade not found"}

            now = datetime.now(timezone.utc).isoformat()
            symbol = trade["symbol"]
            shares = trade["shares_remaining"]
            stop_cents = trade["stop_price_cents"]

            try:
                client = _get_client()

                if enabled:
                    # Enable hold mode: cancel ALL orders, place stop-only
                    _cancel_all_orders(client, conn, trade_id, now)
                    result = client.place_stop_order(symbol, "sell", shares, stop_cents / 100)
                    order_id = str(result.get("id", ""))
                    conn.execute(
                        "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                        "VALUES (?, ?, 'stop', ?, ?, 'open', ?, ?)",
                        (trade_id, order_id, shares, stop_cents, now, now),
                    )
                    conn.execute(
                        "UPDATE trades SET targets_enabled = 0, updated_at = ? WHERE id = ?",
                        (now, trade_id),
                    )
                else:
                    # Disable hold mode: recalculate targets and place OCO + stop
                    atr = _get_atr(symbol)
                    entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]

                    t1_mode = trade["t1_mode"] or "atr"
                    t1_val = trade["t1_mode_value"] or 10.0
                    t2_mode = trade["t2_mode"] or "atr"
                    t2_val = trade["t2_mode_value"] or 15.0
                    t3_mode = trade["t3_mode"] or "atr"
                    t3_val = trade["t3_mode_value"] or 20.0

                    t1 = calculate_target_price(entry_cents, t1_mode, t1_val, atr)
                    t2 = calculate_target_price(entry_cents, t2_mode, t2_val, atr)
                    t3 = calculate_target_price(entry_cents, t3_mode, t3_val, atr)

                    t1_pct = trade["t1_exit_pct"] or 70
                    t2_pct = trade["t2_exit_pct"] or 20
                    t3_pct = trade["t3_exit_pct"] or 10
                    dist = _calculate_share_distribution(shares, t1_pct, t2_pct, t3_pct)

                    _cancel_all_orders(client, conn, trade_id, now)
                    _place_manual_orders(client, trade_id, symbol, shares, stop_cents, t1, t2, t3, dist, True)

                    conn.execute(
                        "UPDATE trades SET targets_enabled = 1, "
                        "target_t1_price_cents = ?, target_t2_price_cents = ?, target_t3_price_cents = ?, "
                        "t1_shares = ?, t2_shares = ?, t3_shares = ?, "
                        "updated_at = ? WHERE id = ?",
                        (t1, t2, t3, dist["t1_shares"], dist["t2_shares"], dist["t3_shares"], now, trade_id),
                    )

            except Exception as e:
                logger.error(f"Hold mode toggle failed for trade {trade_id}: {e}")
                return {"error": str(e)}

    return {"trade_id": trade_id, "hold_mode": enabled}


def close_manual_position(trade_id: int) -> dict:
    """Close a manual position: cancel all orders, market sell shares, close DB record."""
    lock = get_trade_lock(trade_id)
    with lock:
        with get_db() as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND status = 'open'",
                (trade_id,),
            ).fetchone()
            if not trade:
                return {"error": "Trade not found"}

            now = datetime.now(timezone.utc).isoformat()
            symbol = trade["symbol"]
            shares = trade["shares_remaining"]

            try:
                client = _get_client()
                _cancel_all_orders(client, conn, trade_id, now)

                # Market sell remaining shares
                if shares > 0:
                    result = client.place_market_order(symbol, "sell", shares)
                    order_id = str(result.get("id", ""))
                    conn.execute(
                        "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                        "VALUES (?, ?, 'close_sell', ?, NULL, 'open', ?, ?)",
                        (trade_id, order_id, shares, now, now),
                    )
                    logger.info(f"Market sell {shares}sh {symbol} for manual close")
            except Exception as e:
                logger.error(f"Failed to close manual position {trade_id}: {e}")
                return {"error": f"Order placement failed: {e}"}

            conn.execute(
                "UPDATE trades SET status = 'closed', exit_reason = 'manual_close', "
                "position_state = 'CLOSED', close_time = ?, shares_remaining = 0, updated_at = ? WHERE id = ?",
                (now, now, trade_id),
            )

    logger.info(f"Closed manual trade {trade_id} ({symbol})")
    return {"trade_id": trade_id, "closed": True}


def release_position(trade_id: int) -> dict:
    """Release a position: cancel all orders, close DB record, do NOT sell shares."""
    lock = get_trade_lock(trade_id)
    with lock:
        with get_db() as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND trade_type = 'manual' AND status = 'open'",
                (trade_id,),
            ).fetchone()
            if not trade:
                return {"error": "Trade not found"}

            now = datetime.now(timezone.utc).isoformat()
            try:
                client = _get_client()
                _cancel_all_orders(client, conn, trade_id, now)
            except Exception as e:
                logger.warning(f"Could not cancel orders for release of trade {trade_id}: {e}")

            conn.execute(
                "UPDATE trades SET status = 'closed', exit_reason = 'released', "
                "position_state = 'CLOSED', close_time = ?, updated_at = ? WHERE id = ?",
                (now, now, trade_id),
            )

    logger.info(f"Released trade {trade_id} ({trade['symbol']})")
    return {"trade_id": trade_id, "released": True}


def _cancel_all_orders(client, conn, trade_id: int, now: str):
    """Cancel all open orders for a trade on Tradier and mark cancelled in DB."""
    orders = conn.execute(
        "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open'",
        (trade_id,),
    ).fetchall()

    for order in orders:
        try:
            client.cancel_order(int(order["order_id"]))
            client.wait_for_cancel(int(order["order_id"]))
        except Exception as e:
            logger.warning(f"Cancel order {order['order_id']}: {e}")
        conn.execute(
            "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, order["id"]),
        )


def _cancel_and_replace_stops(client, conn, trade_id: int, symbol: str, new_stop_cents: int, shares: int, now: str):
    """Cancel existing stop orders and place new one at updated price."""
    orders = conn.execute(
        "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open' AND order_type IN ('stop', 't1_oco', 't2_oco', 't3_oco')",
        (trade_id,),
    ).fetchall()

    for order in orders:
        try:
            client.cancel_order(int(order["order_id"]))
            client.wait_for_cancel(int(order["order_id"]))
        except Exception as e:
            logger.warning(f"Cancel order {order['order_id']}: {e}")
        conn.execute(
            "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, order["id"]),
        )

    # Place new stop
    result = client.place_stop_order(symbol, "sell", shares, new_stop_cents / 100)
    order_id = str(result.get("id", ""))
    conn.execute(
        "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
        "VALUES (?, ?, 'stop', ?, ?, 'open', ?, ?)",
        (trade_id, order_id, shares, new_stop_cents, now, now),
    )
