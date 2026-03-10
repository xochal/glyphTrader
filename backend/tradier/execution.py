"""
Trade execution: time stops first, then new signals (sorted V4 desc).
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List

import pandas as pd

from config.config_loader import get_trading_params
from core.filters import filter_sector
from core.position_sizer import calculate_position_size, calculate_exit_prices, calculate_share_distribution
from core.regime import check_regime, get_benchmark_index
from data.datastore import DataStore
from db.database import get_db
from db import crypto

logger = logging.getLogger("glyphTrader.execution")


def _get_tradier_client():
    """Get Tradier client using decrypted credentials."""
    from tradier.client import TradierClient
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'tradier_api_token'").fetchone()
        env_row = conn.execute("SELECT value FROM settings WHERE key = 'tradier_environment'").fetchone()
        acct_row = conn.execute("SELECT value FROM settings WHERE key = 'tradier_account_number'").fetchone()

    if not row:
        raise RuntimeError("No Tradier credentials configured")

    token = crypto.decrypt(row["value"])
    environment = env_row["value"] if env_row else "sandbox"
    account = crypto.decrypt(acct_row["value"]) if acct_row else ""

    # Defense-in-depth: force sandbox if production but unlicensed
    if environment == "production":
        from license import is_production_licensed
        if not is_production_licensed():
            logger.error("Production environment without valid license — forcing sandbox")
            environment = "sandbox"

    return TradierClient(api_token=token, account_number=account, environment=environment)


def _get_degraded_client():
    """Get client using degraded-mode token (when system is locked)."""
    from tradier.client import TradierClient
    with get_db() as conn:
        jwt_secret = conn.execute("SELECT value FROM settings WHERE key = 'jwt_secret'").fetchone()
        degraded = conn.execute("SELECT value FROM settings WHERE key = 'degraded_token'").fetchone()
        env_row = conn.execute("SELECT value FROM settings WHERE key = 'tradier_environment'").fetchone()
        acct_row = conn.execute("SELECT value FROM settings WHERE key = 'degraded_account'").fetchone()

    if not jwt_secret or not degraded:
        raise RuntimeError("No degraded-mode credentials available")

    token = crypto.decrypt_with_key(degraded["value"], jwt_secret["value"])
    environment = env_row["value"] if env_row else "sandbox"
    account = crypto.decrypt_with_key(acct_row["value"], jwt_secret["value"]) if acct_row else ""

    # Defense-in-depth: force sandbox if production but unlicensed
    if environment == "production":
        from license import is_production_licensed
        if not is_production_licensed():
            logger.error("Production environment without valid license (degraded) — forcing sandbox")
            environment = "sandbox"

    return TradierClient(api_token=token, account_number=account, environment=environment)


def process_time_stops(client) -> int:
    """
    Process time-based stops BEFORE new signals.
    Returns count of positions closed.
    """
    params = get_trading_params()
    time_stops = params["time_stops"]
    closed = 0
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND trade_type = 'auto' "
            "AND position_state NOT IN ('ADOPTING', 'FLATTEN_PENDING')"
        ).fetchall()

        for trade in open_trades:
            entry_time = datetime.fromisoformat(trade["entry_time"])
            days_held = (now - entry_time).days

            # Hard time stop: 60 calendar days
            if days_held >= time_stops["hard_time_stop_days"]:
                logger.info(f"Hard time stop: {trade['symbol']} held {days_held} days")
                _close_position(client, conn, trade, "time_stop_hard")
                closed += 1
                continue

            # Stagnant win: 20 days, >5% profit, T1 hit (Review Finding #5)
            if (
                days_held >= time_stops["stagnant_win_days"]
                and trade["t1_filled"]  # CRITICAL: T1 must be hit first
            ):
                entry_cents = trade["blended_entry_price_cents"] or trade["entry_price_cents"]
                if entry_cents > 0:
                    store = DataStore()
                    quote = store.get_latest_indicators(trade["symbol"])
                    if quote:
                        current_cents = round(quote.get("close", 0) * 100)
                        profit_pct = (current_cents - entry_cents) / entry_cents * 100
                        if profit_pct < time_stops["stagnant_win_min_profit_pct"]:
                            logger.info(
                                f"Stagnant win stop: {trade['symbol']} "
                                f"{days_held} days, {profit_pct:.1f}% profit"
                            )
                            _close_position(client, conn, trade, "time_stop_stagnant")
                            closed += 1
                            continue

    return closed


def _close_position(client, conn, trade, exit_reason: str):
    """Close a position: cancel orders, market sell, update DB (atomic)."""
    symbol = trade["symbol"]
    shares_remaining = trade["shares_remaining"]

    # Cancel all open orders for this trade
    orders = conn.execute(
        "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open'",
        (trade["id"],),
    ).fetchall()

    for order in orders:
        try:
            client.cancel_order(int(order["order_id"]))
        except Exception as e:
            logger.warning(f"Failed to cancel order {order['order_id']}: {e}")

    # Market sell remaining shares
    if shares_remaining > 0:
        try:
            result = client.place_market_order(symbol, "sell", shares_remaining)
            logger.info(f"Market sell {shares_remaining} {symbol}: {result}")
        except Exception as e:
            logger.error(f"Failed to market sell {symbol}: {e}")

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE trades SET status = 'closed', exit_reason = ?, position_state = 'CLOSED', "
        "close_time = ?, shares_remaining = 0, updated_at = ? WHERE id = ?",
        (exit_reason, now, now, trade["id"]),
    )
    conn.execute(
        "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE trade_id = ? AND status = 'open'",
        (now, trade["id"]),
    )


def execute_signals(signals: List[Dict]) -> int:
    """
    Execute buy signals (already sorted by V4 desc from plan_generator).
    Returns count of entries placed.
    """
    if not crypto.is_unlocked():
        logger.warning("System locked — cannot execute trades")
        return 0

    client = _get_tradier_client()
    params = get_trading_params()

    # Process time stops first
    time_stop_count = process_time_stops(client)
    if time_stop_count:
        logger.info(f"Closed {time_stop_count} positions via time stops")

    # Get available cash
    balances = client.get_balances()
    available_cash = float(balances.get("total_cash", 0))
    available_cash_cents = round(available_cash * 100)

    store = DataStore()
    vix_level = store.get_vix_level()
    if vix_level is None or store.is_vix_stale():
        logger.warning(f"VIX missing or stale — skipping new entries (time stops still processed)")
        return 0

    # Fetch live benchmark quotes for regime re-check
    live_benchmarks = {}
    try:
        bench_quotes = client.get_quotes(["SPY", "QQQ"])
        for q in bench_quotes:
            sym = q.get("symbol")
            last_price = q.get("last")
            if sym and last_price:
                live_benchmarks[sym] = float(last_price)
        if live_benchmarks:
            logger.info(f"Regime recheck: live benchmarks {live_benchmarks}")
    except Exception as e:
        logger.warning(f"Failed to fetch live benchmarks for regime recheck: {e}")

    entries = 0

    for signal in signals:
        symbol = signal["symbol"]
        price_cents = signal["entry_price_cents"]

        # Re-check regime with live benchmark price
        if live_benchmarks:
            bench = get_benchmark_index(symbol)
            if bench in live_benchmarks:
                index_data = store.get_index_data(bench)
                if index_data is not None and not index_data.empty:
                    sma_100 = index_data["SMA_100"].iloc[-1]
                    if pd.notna(sma_100):
                        live_regime = check_regime(vix_level, live_benchmarks[bench], float(sma_100))
                        if signal.get("action") == "buy" and not live_regime["allows_entry"]:
                            logger.info(f"REGIME RECHECK: blocking {symbol} — {live_regime['reason']}")
                            continue
                        if signal.get("action") == "skip":
                            if not live_regime["allows_entry"]:
                                continue  # Still blocked
                            # Regime flipped favorable — check sector filter before promoting
                            sector_ok, sector_reason = filter_sector(symbol)
                            if not sector_ok:
                                logger.info(f"REGIME RECHECK: {symbol} regime OK but {sector_reason}")
                                continue
                            logger.info(f"REGIME RECHECK: promoting {symbol} (V4={signal.get('v4_score', 0):.1f}) — {live_regime['reason']}")
                            signal["action"] = "buy"

        # Skip signals not promoted by regime recheck
        if signal.get("action") == "skip":
            continue

        atr_cents = signal.get("stop_price_cents", 0)

        # Check for existing auto position (pyramid check)
        with get_db() as conn:
            existing = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? AND status = 'open' AND trade_type = 'auto'",
                (symbol,),
            ).fetchone()

        if existing:
            # Pyramid logic
            entries += _try_pyramid(client, conn, existing, signal, vix_level, available_cash_cents)
            continue

        # New position
        shares = calculate_position_size(
            available_cash_cents=available_cash_cents,
            stock_price_cents=price_cents,
            symbol=symbol,
            vix_level=vix_level,
        )

        if shares <= 0:
            logger.info(f"No shares for {symbol} at ${price_cents/100:.2f}")
            continue

        cost_cents = shares * price_cents
        if cost_cents > available_cash_cents:
            logger.info(f"Insufficient cash for {symbol}: need ${cost_cents/100:.2f}")
            continue

        # Place market entry
        try:
            result = client.place_market_order(symbol, "buy", shares)
            order_id = str(result.get("id", ""))
            logger.info(f"Entry: BUY {shares} {symbol} -> order {order_id}")

            exit_prices = calculate_exit_prices(price_cents, round(signal.get("stop_price_cents", price_cents) * 0.1))
            # Recalculate from actual ATR
            latest = store.get_latest_indicators(symbol)
            if latest and latest.get("ATR_14"):
                atr_val = round(latest["ATR_14"] * 100)
                exit_prices = calculate_exit_prices(price_cents, atr_val)

            dist = calculate_share_distribution(shares)
            now = datetime.now(timezone.utc).isoformat()

            with get_db() as conn:
                conn.execute(
                    "INSERT INTO trades (symbol, entry_price_cents, entry_time, shares, shares_remaining, "
                    "stop_price_cents, base_stop_cents, target_t1_price_cents, target_t2_price_cents, "
                    "target_t3_price_cents, original_atr_cents, position_state, t1_shares, t2_shares, t3_shares, "
                    "blended_entry_price_cents, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ENTRY_PENDING', ?, ?, ?, ?, ?, ?)",
                    (symbol, price_cents, now, shares, shares,
                     exit_prices["stop_price_cents"], exit_prices["stop_price_cents"],
                     exit_prices["t1_price_cents"], exit_prices["t2_price_cents"],
                     exit_prices["t3_price_cents"],
                     round((latest.get("ATR_14", 1) * 100)) if latest else 100,
                     dist["t1_shares"], dist["t2_shares"], dist["t3_shares"],
                     price_cents, now, now),
                )
                trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                    "VALUES (?, ?, 'entry', ?, ?, 'open', ?, ?)",
                    (trade_id, order_id, shares, price_cents, now, now),
                )

            available_cash_cents -= cost_cents
            entries += 1
        except Exception as e:
            logger.error(f"Entry failed for {symbol}: {e}")

    logger.info(f"Executed {entries} entries")
    return entries


def _try_pyramid(client, conn_outer, existing, signal, vix_level, available_cash_cents) -> int:
    """Attempt pyramid add to existing position."""
    params = get_trading_params()
    pyramid_params = params["pyramid"]
    symbol = existing["symbol"]

    # Check V4 >= 75 for pyramid (Review Finding #15)
    if signal["v4_score"] < pyramid_params["v4_min_pyramid_score"]:
        return 0

    # Check max pyramids
    if existing["pyramid_count"] >= pyramid_params["max_pyramids_per_position"]:
        # At max position — trailing adjustment (UP only ratchet)
        logger.info(f"{symbol}: max pyramids reached, checking trailing adjustment")
        return 0

    # Calculate pyramid shares
    price_cents = signal["entry_price_cents"]
    shares = calculate_position_size(
        available_cash_cents=available_cash_cents,
        stock_price_cents=price_cents,
        symbol=symbol,
        vix_level=vix_level,
        is_pyramid=True,
    )

    if shares <= 0:
        return 0

    # Check max per stock
    sizing = params["position_sizing"]
    total_value = (existing["shares"] + shares) * price_cents
    max_value = int(available_cash_cents * sizing["max_per_stock_pct"] / 100)
    if total_value > max_value:
        shares = max(0, (max_value - existing["shares"] * price_cents) // price_cents)
        if shares <= 0:
            return 0

    try:
        # Cancel all existing exit orders
        with get_db() as conn:
            orders = conn.execute(
                "SELECT * FROM order_state WHERE trade_id = ? AND status = 'open' AND order_type != 'entry'",
                (existing["id"],),
            ).fetchall()

        for order in orders:
            try:
                client.cancel_order(int(order["order_id"]))
                client.wait_for_cancel(int(order["order_id"]))
            except Exception as e:
                logger.warning(f"Cancel order {order['order_id']}: {e}")

        # Place pyramid buy
        result = client.place_market_order(symbol, "buy", shares)
        order_id = str(result.get("id", ""))
        logger.info(f"Pyramid: BUY {shares} {symbol} -> order {order_id}")

        # Recalculate blended entry and targets
        old_shares = existing["shares"]
        new_total = old_shares + shares
        old_entry = existing["blended_entry_price_cents"] or existing["entry_price_cents"]
        blended_entry = (old_entry * old_shares + price_cents * shares) // new_total

        store = DataStore()
        latest = store.get_latest_indicators(symbol)
        atr_cents = round(latest["ATR_14"] * 100) if latest and latest.get("ATR_14") else existing["original_atr_cents"]
        exit_prices = calculate_exit_prices(blended_entry, atr_cents)
        dist = calculate_share_distribution(new_total)
        now = datetime.now(timezone.utc).isoformat()

        with get_db() as conn:
            # Reset stops/targets/hit flags, update base_stop (Review Finding: pyramid reset)
            conn.execute(
                "UPDATE trades SET shares = ?, shares_remaining = ?, "
                "blended_entry_price_cents = ?, stop_price_cents = ?, base_stop_cents = ?, "
                "target_t1_price_cents = ?, target_t2_price_cents = ?, target_t3_price_cents = ?, "
                "t1_filled = 0, t2_filled = 0, t3_filled = 0, "
                "t1_shares = ?, t2_shares = ?, t3_shares = ?, "
                "original_atr_cents = ?, pyramid_count = pyramid_count + 1, "
                "position_state = 'ENTRY_FILLED', updated_at = ? "
                "WHERE id = ?",
                (new_total, new_total, blended_entry,
                 exit_prices["stop_price_cents"], exit_prices["stop_price_cents"],
                 exit_prices["t1_price_cents"], exit_prices["t2_price_cents"],
                 exit_prices["t3_price_cents"],
                 dist["t1_shares"], dist["t2_shares"], dist["t3_shares"],
                 atr_cents, now, existing["id"]),
            )
            # Cancel old orders in DB
            conn.execute(
                "UPDATE order_state SET status = 'cancelled', updated_at = ? WHERE trade_id = ? AND status = 'open'",
                (now, existing["id"]),
            )
            # Record new entry order
            conn.execute(
                "INSERT INTO order_state (trade_id, order_id, order_type, shares, price_cents, status, created_at, updated_at) "
                "VALUES (?, ?, 'pyramid_entry', ?, ?, 'open', ?, ?)",
                (existing["id"], order_id, shares, price_cents, now, now),
            )

        return 1
    except Exception as e:
        logger.error(f"Pyramid failed for {symbol}: {e}")
        return 0
