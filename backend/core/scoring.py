"""
V4 Scoring Engine — pure pandas/numpy, verbatim constants.
V4 algorithmic scorer — exact constants, do not modify.
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional

# OPTIMIZED V4 WEIGHTS (Trial #933, Calmar 2.50 training, 2.37 validation)
BASE_SCORE = 1.908
WEIGHT_SLOPE = 40.905
WEIGHT_STOCH = 25.763
WEIGHT_RSI = 22.232
WEIGHT_ADX = 38.900

# BB thresholds and points
BB_THRESHOLD_LOW = 0.131
BB_THRESHOLD_HIGH = 0.365
BB_POINTS_LOW = 19.399
BB_POINTS_HIGH = 8.971

# Volume thresholds and points
VOL_THRESHOLD_LOW = 1.185
VOL_THRESHOLD_MID = 1.016
VOL_THRESHOLD_HIGH = 1.327
VOL_POINTS_LOW = 2.784
VOL_POINTS_MID = 1.900
VOL_POINTS_HIGH = 7.462

# Overbought settings
OVERBOUGHT_STOCH_THRESHOLD = 92.351
OVERBOUGHT_RSI_THRESHOLD = 79.824
OVERBOUGHT_PENALTY = -23.598

# Extended settings
EXTENDED_BB_THRESHOLD = 0.936
EXTENDED_PENALTY = -0.953


def rolling_percentile(series: pd.Series, window: int = 252) -> pd.Series:
    """
    Rolling percentile rank with NO look-ahead bias.
    Uses expanding window with max cap — only data seen so far.
    """
    def percentile_rank(x):
        if len(x) < 2:
            return 0.5
        lookback_window = min(len(x), window)
        lookback_data = x.iloc[-lookback_window:]
        return (lookback_data.iloc[-1] > lookback_data.iloc[:-1]).sum() / (len(lookback_data) - 1)

    return series.expanding(min_periods=20).apply(percentile_rank, raw=False)


def calculate_v4_score(df: pd.DataFrame, rolling_window: int = 252) -> pd.Series:
    """
    Calculate V4 algorithmic score.
    Input df must have: close, high, low, volume, LINEAR_REG_SLOPE, STOCH_K, RSI_D, ADX, BB_PCT, VOL_SURGE
    Returns: Score series [0, 100]
    """
    # Ensure features exist
    from core.indicators import stochastic_k, volume_surge
    if "STOCH_K" not in df.columns:
        df = df.copy()
        df["STOCH_K"] = stochastic_k(df["high"], df["low"], df["close"])
    if "BB_PCT" not in df.columns:
        df = df.copy()
        from core.indicators import bollinger_bands
        _, _, _, df["BB_PCT"] = bollinger_bands(df["close"])
    if "VOL_SURGE" not in df.columns:
        df = df.copy()
        df["VOL_SURGE"] = volume_surge(df["volume"])

    score = pd.Series(BASE_SCORE, index=df.index)

    # Rolling percentiles
    slope_pct = rolling_percentile(df["LINEAR_REG_SLOPE"], rolling_window)
    stoch_pct = 1 - rolling_percentile(df["STOCH_K"], rolling_window)
    rsi_pct = 1 - rolling_percentile(df["RSI_D"], rolling_window)
    adx_pct = rolling_percentile(df["ADX"], rolling_window)

    # Component 1: Trend
    score += slope_pct * WEIGHT_SLOPE

    # Component 2: Oversold
    score += stoch_pct * WEIGHT_STOCH
    score += rsi_pct * WEIGHT_RSI

    # Component 3: Trend strength
    score += adx_pct * WEIGHT_ADX

    # Component 4: BB position
    bb_pct = df["BB_PCT"]
    bb_score = np.where(bb_pct < BB_THRESHOLD_LOW, BB_POINTS_LOW,
                np.where(bb_pct < BB_THRESHOLD_HIGH, BB_POINTS_HIGH, 0))
    score += bb_score

    # Component 5: Volume surge
    vol_surge = df["VOL_SURGE"]
    vol_bonus = np.where(vol_surge > VOL_THRESHOLD_HIGH, VOL_POINTS_HIGH,
                 np.where(vol_surge > VOL_THRESHOLD_MID, VOL_POINTS_MID,
                 np.where(vol_surge > VOL_THRESHOLD_LOW, VOL_POINTS_LOW, 0)))
    score += vol_bonus

    # Penalty 1: Overbought
    overbought = (df["STOCH_K"] > OVERBOUGHT_STOCH_THRESHOLD) | (df["RSI_D"] > OVERBOUGHT_RSI_THRESHOLD)
    score += overbought.astype(int) * OVERBOUGHT_PENALTY

    # Penalty 2: Extended BB
    extended = bb_pct > EXTENDED_BB_THRESHOLD
    score += extended.astype(int) * EXTENDED_PENALTY

    return score.clip(0, 100)


def calculate_v4_for_latest(df: pd.DataFrame) -> Tuple[float, str]:
    scores = calculate_v4_score(df)
    latest = float(scores.iloc[-1])
    confidence = "HIGH" if latest >= 60 else ("MEDIUM" if latest >= 40 else "LOW")
    return latest, confidence
