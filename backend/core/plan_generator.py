"""
Daily signal generation: DataStore -> filter cascade -> sort by V4 desc -> trade decisions.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List

import pandas as pd
import numpy as np

from config.config_loader import get_trading_params, get_watchlist
from core.filters import run_filter_cascade
from core.regime import check_regime, get_benchmark_index
from core.position_sizer import calculate_position_size, calculate_exit_prices
from data.datastore import DataStore
from db.database import get_db

logger = logging.getLogger("glyphTrader.plan_generator")


def generate_daily_plans() -> List[Dict]:
    """
    Generate trading plans for today.
    Returns list of plan dicts (buy signals sorted by V4 desc).
    """
    store = DataStore()
    wl = get_watchlist()
    params = get_trading_params()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Data freshness gates (refuse to trade on stale data) ---

    if not store.is_fresh(today):
        logger.warning("DataStore not updated today — skipping plan generation")
        return []
    vix_level = store.get_vix_level()
    if vix_level is None:
        logger.warning("No VIX data — skipping plan generation")
        return []
    if store.is_vix_stale():
        logger.warning(f"VIX data is stale — skipping plan generation (VIX={vix_level:.1f})")
        return []

    # Verify benchmarks are present and have SMA_100
    for bench_sym in wl.get("benchmark_symbols", ["SPY", "QQQ"]):
        bench_data = store.get_index_data(bench_sym)
        if bench_data is None or bench_data.empty:
            logger.warning(f"No {bench_sym} data — skipping plan generation")
            return []
        if "SMA_100" not in bench_data.columns or pd.isna(bench_data["SMA_100"].iloc[-1]):
            logger.warning(f"{bench_sym} SMA_100 unavailable — skipping plan generation")
            return []
        if store.is_symbol_stale(bench_sym):
            logger.warning(f"{bench_sym} data is stale — skipping plan generation")
            return []

    # --- Trading enabled check ---

    with get_db() as conn:
        trading_enabled = conn.execute(
            "SELECT value FROM settings WHERE key = 'trading_enabled'"
        ).fetchone()
        if not trading_enabled or trading_enabled["value"] != "true":
            logger.info("Trading disabled — skipping plan generation")
            return []

    plans = []
    candidates = []

    for stock in wl["stocks"]:
        symbol = stock["symbol"]
        df = store.get_enriched(symbol)
        if df is None or df.empty:
            continue

        # Skip symbols with stale data (fetch failed, old data lingering)
        if store.is_symbol_stale(symbol):
            logger.debug(f"Skipping {symbol} — data is stale")
            continue

        # Get V4 score — must exist and not be NaN
        if "V4_SCORE" not in df.columns:
            continue
        v4_score = df["V4_SCORE"].iloc[-1]
        if pd.isna(v4_score):
            logger.debug(f"Skipping {symbol} — V4_SCORE is NaN (insufficient history)")
            continue
        v4_score = float(v4_score)

        # Get benchmark regime
        bench = get_benchmark_index(symbol)
        index_data = store.get_index_data(bench)
        if index_data is None or index_data.empty:
            continue

        latest_index = index_data.iloc[-1]
        index_price = float(latest_index["close"])
        sma_100 = latest_index.get("SMA_100")
        if sma_100 is None or pd.isna(sma_100):
            logger.debug(f"Skipping {symbol} — {bench} SMA_100 is NaN")
            continue
        sma_100 = float(sma_100)
        regime_result = check_regime(vix_level, index_price, sma_100)

        latest = df.iloc[-1]
        current_price = float(latest["close"])
        signal_price = current_price  # Same-day signal
        atr = latest.get("ATR_14")
        if atr is None or pd.isna(atr) or float(atr) <= 0:
            continue
        atr = float(atr)

        # Estimate required capital
        price_cents = round(current_price * 100)
        atr_cents = round(atr * 100)
        estimated_shares = calculate_position_size(
            available_cash_cents=10000000,  # Placeholder for filter check
            stock_price_cents=price_cents,
            symbol=symbol,
            vix_level=vix_level,
        )
        required_cents = estimated_shares * price_cents

        # Run filter cascade
        passes, skip_reason = run_filter_cascade(
            symbol=symbol,
            v4_score=v4_score,
            df=df,
            current_price=current_price,
            signal_price=signal_price,
            regime_result=regime_result,
            available_cash_cents=10000000,  # Cash filter checked during execution
            required_cents=required_cents,
        )

        exit_prices = calculate_exit_prices(price_cents, atr_cents)

        plan = {
            "date": today,
            "symbol": symbol,
            "v4_score": v4_score,
            "action": "buy" if passes else "skip",
            "skip_reason": skip_reason,
            "entry_price_cents": price_cents,
            "stop_price_cents": exit_prices["stop_price_cents"],
            "t1_price_cents": exit_prices["t1_price_cents"],
            "t2_price_cents": exit_prices["t2_price_cents"],
            "t3_price_cents": exit_prices["t3_price_cents"],
            "shares": estimated_shares if passes else 0,
        }
        plans.append(plan)

        if passes:
            candidates.append(plan)

    # Sort candidates by V4 score descending (Review Finding #12)
    candidates.sort(key=lambda x: x["v4_score"], reverse=True)

    # Store plans in DB
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        for p in plans:
            conn.execute(
                "INSERT INTO daily_plans (date, symbol, v4_score, action, skip_reason, "
                "entry_price_cents, stop_price_cents, t1_price_cents, t2_price_cents, "
                "t3_price_cents, shares, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p["date"], p["symbol"], p["v4_score"], p["action"], p["skip_reason"],
                 p["entry_price_cents"], p["stop_price_cents"], p["t1_price_cents"],
                 p["t2_price_cents"], p["t3_price_cents"], p["shares"], now),
            )

    logger.info(f"Generated {len(plans)} plans ({len(candidates)} buy signals)")
    return candidates
