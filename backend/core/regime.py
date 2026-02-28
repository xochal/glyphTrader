"""
Regime detector: VIX level + Index SMA, stock-to-index from watchlist.json config.
"""

import logging
from typing import Dict

from config.config_loader import get_trading_params, get_watchlist

logger = logging.getLogger("glyphTrader.regime")


def get_benchmark_index(symbol: str) -> str:
    wl = get_watchlist()
    for stock in wl["stocks"]:
        if stock["symbol"] == symbol:
            return stock["benchmark_index"]
    return "SPY"


def check_regime(
    vix_level: float,
    index_price: float,
    index_sma100: float,
) -> Dict:
    """
    Check if regime allows entry.

    Logic (from BUILD_PLAN):
    - If VIX >= 32: ALWAYS block (PANIC)
    - Otherwise: allow if (Index > SMA100) OR (VIX < 20)
    """
    params = get_trading_params()
    vix_max = params["filters"]["regime_vix_max"]
    vix_allow_below = params["filters"]["regime_vix_allow_below"]

    # PANIC override
    if vix_level >= vix_max:
        return {
            "allows_entry": False,
            "regime_type": "PANIC",
            "reason": f"VIX >= {vix_max} ({vix_level:.1f})",
            "vix_level": vix_level,
        }

    # OR logic: (Index > SMA100) OR (VIX < allow_below)
    index_above_sma = index_price > index_sma100
    vix_below_threshold = vix_level < vix_allow_below

    allows = index_above_sma or vix_below_threshold

    if allows:
        reason = []
        if index_above_sma:
            reason.append(f"Index ({index_price:.2f}) > SMA100 ({index_sma100:.2f})")
        if vix_below_threshold:
            reason.append(f"VIX ({vix_level:.1f}) < {vix_allow_below}")
        return {
            "allows_entry": True,
            "regime_type": "FAVORABLE",
            "reason": " OR ".join(reason),
            "vix_level": vix_level,
        }
    else:
        return {
            "allows_entry": False,
            "regime_type": "UNFAVORABLE",
            "reason": f"Index ({index_price:.2f}) <= SMA100 ({index_sma100:.2f}) AND VIX ({vix_level:.1f}) >= {vix_allow_below}",
            "vix_level": vix_level,
        }
