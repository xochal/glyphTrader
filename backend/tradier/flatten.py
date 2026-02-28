"""
Emergency flatten: sell all positions, password-protected, time-aware.
Global flatten lock prevents concurrent execution.
"""

import threading
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db.database import get_db
from db import crypto
from api.auth import _get_setting, _audit

logger = logging.getLogger("glyphTrader.flatten")

ET = ZoneInfo("America/New_York")
_flatten_lock = threading.Lock()


def _get_market_mode(client) -> str:
    """Determine market state: 'market', 'pre', 'post', 'closed'."""
    try:
        if client.is_market_open():
            return "market"
    except Exception:
        pass

    # Fallback to local ET time
    now_et = datetime.now(ET)
    hour = now_et.hour
    minute = now_et.minute
    weekday = now_et.weekday()

    if weekday >= 5:  # Saturday/Sunday
        return "closed"
    if hour < 4 or (hour >= 20):
        return "closed"
    if hour < 9 or (hour == 9 and minute < 30):
        return "pre"
    if hour >= 16:
        return "post"
    return "market"


def flatten_all_positions(password: str, ip: str) -> dict:
    """
    Emergency liquidation of all positions.
    Requires: kill switch ON (trading disabled), valid password, crypto unlocked.
    """
    # Global lock — non-blocking
    if not _flatten_lock.acquire(blocking=False):
        return {"error": "Flatten already in progress", "status_code": 409}

    try:
        return _do_flatten(password, ip)
    finally:
        _flatten_lock.release()


def _do_flatten(password: str, ip: str) -> dict:
    """Core flatten logic under lock."""
    import bcrypt

    # Verify crypto is unlocked
    if not crypto.is_unlocked():
        return {"error": "System is locked", "status_code": 423}

    # Verify kill switch is ON (trading disabled)
    with get_db() as conn:
        if _get_setting(conn, "trading_enabled") != "false":
            return {"error": "Kill switch must be ON (trading disabled) before flattening", "status_code": 400}

        # Verify password
        pw_hash = _get_setting(conn, "admin_password_hash")
        if not pw_hash or not bcrypt.checkpw(password.encode(), pw_hash.encode()):
            return {"error": "Invalid password", "status_code": 401}

        # Rate limit: max 1 per 5 minutes
        rate_check = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_log "
            "WHERE event_type = 'flatten_all' AND created_at > datetime('now', '-5 minutes')"
        ).fetchone()["cnt"]
        if rate_check >= 1:
            return {"error": "Flatten rate limit: max 1 per 5 minutes", "status_code": 429}

    # Get client
    from tradier.execution import _get_tradier_client
    client = _get_tradier_client()

    # Determine market mode
    market_mode = _get_market_mode(client)
    logger.info(f"Flatten All initiated — market mode: {market_mode}")

    # Get all open trades, sorted by id for lock ordering (FLAT-C4)
    with get_db() as conn:
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY id ASC"
        ).fetchall()

    if not open_trades:
        with get_db() as conn:
            _audit(conn, "flatten_all", ip, {"result": "no_positions"})
        return {"positions_processed": 0, "message": "No open positions to flatten"}

    results = []
    from tradier.manual_trades import get_trade_lock

    for trade in open_trades:
        trade_id = trade["id"]
        symbol = trade["symbol"]
        shares = trade["shares_remaining"]

        if shares <= 0:
            continue

        # Skip if already being flattened
        if trade["position_state"] == "FLATTEN_PENDING":
            results.append({"symbol": symbol, "trade_id": trade_id, "status": "already_pending"})
            continue

        # FLAT-M5: ADOPTING trades — close DB record, sell if Tradier has position
        if trade["position_state"] == "ADOPTING":
            lock = get_trade_lock(trade_id)
            with lock:
                now = datetime.now(timezone.utc).isoformat()
                with get_db() as conn:
                    # Cancel any partial orders
                    partial_orders = conn.execute(
                        "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open'", (trade_id,)
                    ).fetchall()
                    for o in partial_orders:
                        try:
                            client.cancel_order(int(o["order_id"]))
                        except Exception:
                            pass
                        conn.execute("UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?", (now, o["id"]))

                    # Check if Tradier actually has shares for this symbol
                    try:
                        positions = client.get_positions()
                        tradier_has = any(p["symbol"] == symbol for p in positions)
                    except Exception:
                        tradier_has = False

                    if tradier_has and shares > 0:
                        try:
                            sell_result = client.place_market_order(symbol, "sell", shares)
                            logger.info(f"Flatten ADOPTING: sold {shares}sh {symbol}")
                        except Exception as e:
                            logger.error(f"Flatten ADOPTING sell failed for {symbol}: {e}")

                    conn.execute(
                        "UPDATE trades SET status = 'closed', exit_reason = 'flatten', "
                        "position_state = 'CLOSED', close_time = ?, shares_remaining = 0, updated_at = ? WHERE id = ?",
                        (now, now, trade_id),
                    )
            results.append({"symbol": symbol, "trade_id": trade_id, "status": "closed", "note": "was_adopting"})
            continue

        lock = get_trade_lock(trade_id)
        with lock:
            try:
                result = _flatten_single_position(client, trade, market_mode)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to flatten {symbol}: {e}")
                results.append({"symbol": symbol, "trade_id": trade_id, "status": "error", "error": str(e)})

    # Audit summary
    with get_db() as conn:
        _audit(conn, "flatten_all", ip, {
            "market_mode": market_mode,
            "positions_processed": len(results),
            "results": [r.get("status") for r in results],
        })

    return {
        "positions_processed": len(results),
        "market_mode": market_mode,
        "results": results,
    }


def _flatten_single_position(client, trade, market_mode: str) -> dict:
    """Flatten a single position atomically under its per-trade lock."""
    trade_id = trade["id"]
    symbol = trade["symbol"]
    shares = trade["shares_remaining"]
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        # 1. Cancel all open orders
        orders = conn.execute(
            "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open'",
            (trade_id,),
        ).fetchall()

        for order in orders:
            try:
                client.cancel_order(int(order["order_id"]))
            except Exception as e:
                logger.warning(f"Cancel order {order['order_id']} for flatten: {e}")
            conn.execute(
                "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, order["id"]),
            )

        # 2. Place sell order based on market mode
        if market_mode == "market":
            # Market order — instant fill
            result = client.place_market_order(symbol, "sell", shares)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 'flatten_sell', ?, NULL, 'open', ?, ?)",
                (trade_id, order_id, shares, now, now),
            )
            # Close immediately (market order fills instantly)
            conn.execute(
                "UPDATE trades SET status = 'closed', exit_reason = 'flatten', "
                "position_state = 'CLOSED', close_time = ?, shares_remaining = 0, updated_at = ? WHERE id = ?",
                (now, now, trade_id),
            )
            logger.info(f"Flatten: {symbol} {shares}sh market sell")
            return {"symbol": symbol, "trade_id": trade_id, "status": "closed", "order_type": "market"}

        elif market_mode in ("pre", "post"):
            # Limit order at bid * 0.995, extended hours
            quote = client.get_quote(symbol)
            bid = float(quote.get("bid", 0)) if quote else 0
            last = float(quote.get("last", 0)) if quote else 0
            entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
            floor_price = entry_cents * 0.50 / 100  # 50% of entry as floor
            # FLAT-H4: fall back to last if bid unavailable
            base_price = bid if bid > 0 else last
            limit_price = max(round(base_price * 0.995, 2), floor_price) if base_price > 0 else floor_price

            duration = "post" if market_mode == "post" else "pre"
            result = client.place_limit_order(symbol, "sell", shares, limit_price, duration=duration)
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 'flatten_sell', ?, ?, 'open', ?, ?)",
                (trade_id, order_id, shares, round(limit_price * 100), now, now),
            )
            conn.execute(
                "UPDATE trades SET position_state = 'FLATTEN_PENDING', updated_at = ? WHERE id = ?",
                (now, trade_id),
            )
            logger.info(f"Flatten: {symbol} {shares}sh limit ${limit_price:.2f} ({market_mode} hours)")
            return {"symbol": symbol, "trade_id": trade_id, "status": "pending", "order_type": "limit", "price": limit_price}

        else:
            # Closed market — GTC limit at last * 0.90
            quote = client.get_quote(symbol)
            last = float(quote.get("last", 0)) if quote else 0
            if last <= 0:
                entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
                last = entry_cents / 100

            limit_price = round(last * 0.90, 2)
            result = client.place_limit_order(symbol, "sell", shares, limit_price, duration="gtc")
            order_id = str(result.get("id", ""))
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 'flatten_sell', ?, ?, 'open', ?, ?)",
                (trade_id, order_id, shares, round(limit_price * 100), now, now),
            )
            conn.execute(
                "UPDATE trades SET position_state = 'FLATTEN_PENDING', updated_at = ? WHERE id = ?",
                (now, trade_id),
            )
            logger.info(f"Flatten: {symbol} {shares}sh GTC limit ${limit_price:.2f} (market closed)")
            return {"symbol": symbol, "trade_id": trade_id, "status": "pending", "order_type": "gtc_limit", "price": limit_price}
