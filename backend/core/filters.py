"""
Filter cascade — 7 filters in exact order.
All pure DataFrame operations, no SQL.
"""

import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from config.config_loader import get_trading_params, get_watchlist

logger = logging.getLogger("glyphTrader.filters")


def filter_v4_score(symbol: str, v4_score: float) -> Tuple[bool, str]:
    """Filter 1: V4 Score with sector-specific thresholds (68-82)."""
    wl = get_watchlist()
    threshold = 75  # default
    for stock in wl["stocks"]:
        if stock["symbol"] == symbol:
            threshold = stock["v4_threshold"]
            break
    if v4_score >= threshold:
        return True, ""
    return False, f"V4 score {v4_score:.1f} < threshold {threshold}"


def filter_ema_crossover(df: pd.DataFrame) -> Tuple[bool, str]:
    """Filter 2: EMA 5x13 crossover with close_only confirmation."""
    if len(df) < 2:
        return False, "Insufficient data for EMA crossover"
    # close_only mode: EMA_5 > EMA_13 on close prices
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    ema5_now = latest.get("EMA_5")
    ema13_now = latest.get("EMA_13")
    ema5_prev = prev.get("EMA_5")
    ema13_prev = prev.get("EMA_13")
    if pd.isna(ema5_now) or pd.isna(ema13_now):
        return False, "EMA values not available"
    # Crossover: EMA_5 crosses above EMA_13
    if ema5_now > ema13_now and ema5_prev <= ema13_prev:
        return True, ""
    # Already above (confirmation): EMA_5 above EMA_13
    if ema5_now > ema13_now:
        return True, ""
    return False, f"EMA_5 ({ema5_now:.2f}) <= EMA_13 ({ema13_now:.2f})"


def filter_price_movement(df: pd.DataFrame, params: Dict) -> Tuple[bool, str]:
    """Filter 3: Price Movement — 0.6x ATR overnight change limit."""
    if len(df) < 2:
        return False, "Insufficient data for price movement"
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    atr_mult = params["filters"]["price_movement_atr_mult"]
    atr_val = latest.get("ATR_14", 0)
    if pd.isna(atr_val) or atr_val == 0:
        return True, ""  # Can't check, pass through
    overnight_change = abs(latest["open"] - prev["close"])
    max_change = atr_mult * atr_val
    if overnight_change <= max_change:
        return True, ""
    return False, f"Overnight change ${overnight_change:.2f} > {atr_mult}x ATR (${max_change:.2f})"


def filter_slippage(current_price: float, signal_price: float, params: Dict) -> Tuple[bool, str]:
    """Filter 4: Slippage — 5% max gap between signal price and entry price."""
    max_slip = params["filters"]["max_slippage_pct"] / 100
    if signal_price == 0:
        return True, ""
    gap = abs(current_price - signal_price) / signal_price
    if gap <= max_slip:
        return True, ""
    return False, f"Slippage {gap*100:.1f}% > max {params['filters']['max_slippage_pct']}%"


def filter_regime(regime_result: Dict) -> Tuple[bool, str]:
    """Filter 5: Regime — uses pre-computed regime check result."""
    if regime_result["allows_entry"]:
        return True, ""
    return False, f"Regime: {regime_result['reason']}"


def filter_sector(symbol: str) -> Tuple[bool, str]:
    """Filter 6: Sector — blocks TIER_5 only."""
    wl = get_watchlist()
    params = get_trading_params()
    blocked_tiers = params["filters"]["blocked_tiers"]
    for stock in wl["stocks"]:
        if stock["symbol"] == symbol:
            if stock["tier"] in blocked_tiers:
                return False, f"Tier {stock['tier']} is blocked"
            return True, ""
    return True, ""  # Unknown symbol passes


def filter_cash(available_cash_cents: int, required_cents: int) -> Tuple[bool, str]:
    """Filter 7: Cash — sufficient capital available."""
    if available_cash_cents >= required_cents:
        return True, ""
    return False, f"Insufficient cash: ${available_cash_cents/100:.2f} < ${required_cents/100:.2f}"


def run_filter_cascade(
    symbol: str,
    v4_score: float,
    df: pd.DataFrame,
    current_price: float,
    signal_price: float,
    regime_result: Dict,
    available_cash_cents: int,
    required_cents: int,
) -> Tuple[bool, str]:
    """
    Run all 7 filters in exact order.
    Returns (passes, skip_reason) — skip_reason is empty string if passes.
    """
    params = get_trading_params()

    # 1. V4 Score
    ok, reason = filter_v4_score(symbol, v4_score)
    if not ok:
        return False, reason

    # 2. EMA Crossover
    ok, reason = filter_ema_crossover(df)
    if not ok:
        return False, reason

    # 3. Price Movement
    ok, reason = filter_price_movement(df, params)
    if not ok:
        return False, reason

    # 4. Slippage
    ok, reason = filter_slippage(current_price, signal_price, params)
    if not ok:
        return False, reason

    # 5. Regime
    ok, reason = filter_regime(regime_result)
    if not ok:
        return False, reason

    # 6. Sector
    ok, reason = filter_sector(symbol)
    if not ok:
        return False, reason

    # 7. Cash
    ok, reason = filter_cash(available_cash_cents, required_cents)
    if not ok:
        return False, reason

    return True, ""
