"""
Technical indicator calculations — pure pandas/numpy/scipy.
SMA, EMA, RSI, ADX, ATR, Stochastic, Bollinger, LINEAR_REG_SLOPE, VOL_SURGE.
"""

import numpy as np
import pandas as pd
from scipy.stats import linregress


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (not simple RSI)."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def stochastic_k(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Stochastic %K (raw, unsmoothed) — V4 uses this, NOT %D."""
    lowest = low.rolling(window=period).min()
    highest = high.rolling(window=period).max()
    return 100 * (close - lowest) / (highest - lowest + 1e-6)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index with Wilder's smoothing."""
    plus_dm = high.diff()
    minus_dm = low.diff().multiply(-1)
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = true_range(high, low, close)
    atr_val = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / (atr_val + 1e-10)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / (atr_val + 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR with Wilder's smoothing."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def bollinger_bands(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    """Returns (upper, middle, lower, pct)."""
    middle = sma(close, period)
    std = close.rolling(window=period).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    pct = (close - lower) / (upper - lower + 1e-6)
    return upper, middle, lower, pct.clip(0, 1)


def linear_reg_slope(close: pd.Series, period: int = 20) -> pd.Series:
    """20-day linear regression slope."""
    def _slope(y):
        if len(y) < period:
            return np.nan
        x = np.arange(len(y))
        slope, _, _, _, _ = linregress(x, y)
        return slope
    return close.rolling(window=period).apply(_slope, raw=True)


def volume_surge(volume: pd.Series, period: int = 20) -> pd.Series:
    vol_ma = volume.rolling(period).mean()
    return volume / (vol_ma + 1e-6)


def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate ALL indicators from raw OHLCV bars.
    Input df must have: open, high, low, close, volume columns.
    Returns enriched DataFrame with all indicator columns.
    """
    df = df.copy()

    # SMAs
    for p in [20, 50, 100, 200]:
        df[f"SMA_{p}"] = sma(df["close"], p)

    # EMAs
    for p in [5, 13, 21]:
        df[f"EMA_{p}"] = ema(df["close"], p)

    # RSI
    df["RSI_D"] = rsi(df["close"], 14)

    # Stochastic %K (raw, used by V4)
    df["STOCH_K"] = stochastic_k(df["high"], df["low"], df["close"], 14)

    # ADX
    df["ADX"] = adx(df["high"], df["low"], df["close"], 14)

    # ATR
    df["ATR_14"] = atr(df["high"], df["low"], df["close"], 14)

    # Bollinger Bands
    df["BB_UPPER"], df["BB_MIDDLE"], df["BB_LOWER"], df["BB_PCT"] = bollinger_bands(df["close"], 20, 2.0)

    # Linear Regression Slope
    df["LINEAR_REG_SLOPE"] = linear_reg_slope(df["close"], 20)

    # Volume Surge
    df["VOL_SURGE"] = volume_surge(df["volume"], 20)

    return df
