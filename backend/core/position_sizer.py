"""
Position sizing: cash-based + VIX dynamic brackets + sector tier multipliers + whole shares.
"""

import math
import logging
from typing import Dict

from config.config_loader import get_trading_params, get_watchlist

logger = logging.getLogger("glyphTrader.position_sizer")


def get_vix_multiplier(vix_level: float) -> float:
    """VIX dynamic sizing brackets: [16, 22, 28, 34] -> [1.0, 0.8, 0.6, 0.4, 0.2]"""
    params = get_trading_params()
    brackets = params["vix_sizing_brackets"]
    multipliers = params["vix_sizing_multipliers"]
    for i, threshold in enumerate(brackets):
        if vix_level < threshold:
            return multipliers[i]
    return multipliers[-1]


def get_tier_multiplier(symbol: str) -> float:
    """Get sector tier size multiplier from watchlist.json."""
    wl = get_watchlist()
    for stock in wl["stocks"]:
        if stock["symbol"] == symbol:
            return stock.get("tier_size_multiplier", 1.0)
    return 1.0


def calculate_position_size(
    available_cash_cents: int,
    stock_price_cents: int,
    symbol: str,
    vix_level: float,
    is_pyramid: bool = False,
) -> int:
    """
    Calculate position size in whole shares.

    Returns number of shares (integer, whole shares only).
    """
    params = get_trading_params()
    sizing = params["position_sizing"]

    pct = sizing["pyramid_pct"] if is_pyramid else sizing["initial_pct"]
    base_amount_cents = int(available_cash_cents * pct / 100)

    # VIX adjustment
    vix_mult = get_vix_multiplier(vix_level)
    adjusted_cents = int(base_amount_cents * vix_mult)

    # Tier multiplier
    tier_mult = get_tier_multiplier(symbol)
    adjusted_cents = int(adjusted_cents * tier_mult)

    # Calculate whole shares
    if stock_price_cents <= 0:
        return 0
    shares = adjusted_cents // stock_price_cents
    return max(shares, 0)


def calculate_share_distribution(total_shares: int) -> Dict[str, int]:
    """
    70/20/10 split with small-position rounding rules.
    """
    params = get_trading_params()
    exits = params["atr_exits"]
    t1_pct = exits["t1_exit_pct"] / 100
    t2_pct = exits["t2_exit_pct"] / 100
    t3_pct = exits["t3_exit_pct"] / 100

    if total_shares <= 0:
        return {"t1_shares": 0, "t2_shares": 0, "t3_shares": 0}

    # Small positions: all to T1
    if total_shares <= 3:
        return {"t1_shares": total_shares, "t2_shares": 0, "t3_shares": 0}

    # Calculate splits
    t3_shares = max(0, math.floor(total_shares * t3_pct))
    t2_shares = max(0, math.floor(total_shares * t2_pct))

    # Minimums
    if total_shares >= 10 and t3_shares < 1:
        t3_shares = 1
    if total_shares >= 5 and t2_shares < 1:
        t2_shares = 1

    # T3 must be <= T2
    if t3_shares > t2_shares:
        t3_shares, t2_shares = t2_shares, t3_shares

    # T1 gets remainder
    t1_shares = total_shares - t2_shares - t3_shares

    return {"t1_shares": t1_shares, "t2_shares": t2_shares, "t3_shares": t3_shares}


def calculate_exit_prices(entry_price_cents: int, atr_cents: int) -> Dict[str, int]:
    """
    Calculate stop and target prices in cents.
    ATR targets: Stop 3.3x below, T1 0.7x above, T2 1.5x above, T3 3.0x above.
    """
    params = get_trading_params()
    exits = params["atr_exits"]

    # Calculate in float for precision, convert to cents at the end (Review Finding #6)
    entry = entry_price_cents / 100
    atr_val = atr_cents / 100

    stop = entry - exits["stop_loss_mult"] * atr_val
    t1 = entry + exits["t1_target_mult"] * atr_val
    t2 = entry + exits["t2_target_mult"] * atr_val
    t3 = entry + exits["t3_target_mult"] * atr_val

    return {
        "stop_price_cents": max(1, round(stop * 100)),
        "t1_price_cents": round(t1 * 100),
        "t2_price_cents": round(t2 * 100),
        "t3_price_cents": round(t3 * 100),
    }
