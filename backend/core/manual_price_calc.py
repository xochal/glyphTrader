"""
Price calculator for manual trades: ATR/dollar/percent stop, target, and ratchet.
All prices in integer cents. Validates NaN/zero/negative.
"""

import math
import logging

logger = logging.getLogger("glyphTrader.manual_price_calc")

VALID_MODES = {"atr", "dollar", "percent"}


def _validate_atr(atr: float):
    """Reject NaN, zero, negative, or infinite ATR (SEC-9)."""
    if atr is None or math.isnan(atr) or math.isinf(atr) or atr <= 0:
        raise ValueError(f"Invalid ATR value: {atr}")


def _validate_mode(mode: str):
    """Reject unknown modes (SEC-5)."""
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode: {mode} (must be one of {VALID_MODES})")


def _validate_result(price_cents: int, label: str):
    """Ensure result is positive and finite."""
    if price_cents <= 0 or math.isinf(price_cents) or math.isnan(price_cents):
        raise ValueError(f"Calculated {label} is invalid: {price_cents}")


def calculate_stop_price(entry_cents: int, mode: str, value: float, atr: float) -> int:
    """Calculate stop price in cents. Result must be below entry."""
    _validate_mode(mode)

    if mode == "atr":
        _validate_atr(atr)
        atr_cents = round(atr * 100)
        stop_cents = entry_cents - round(value * atr_cents)
    elif mode == "dollar":
        stop_cents = entry_cents - round(value * 100)
    elif mode == "percent":
        stop_cents = round(entry_cents * (1 - value / 100))

    _validate_result(stop_cents, "stop")

    if stop_cents >= entry_cents:
        raise ValueError(f"Stop ({stop_cents}) must be below entry ({entry_cents})")

    return stop_cents


def calculate_target_price(entry_cents: int, mode: str, value: float, atr: float) -> int:
    """Calculate target price in cents. Result must be above entry."""
    _validate_mode(mode)

    if mode == "atr":
        _validate_atr(atr)
        atr_cents = round(atr * 100)
        target_cents = entry_cents + round(value * atr_cents)
    elif mode == "dollar":
        target_cents = entry_cents + round(value * 100)
    elif mode == "percent":
        target_cents = round(entry_cents * (1 + value / 100))

    _validate_result(target_cents, "target")

    if target_cents <= entry_cents:
        raise ValueError(f"Target ({target_cents}) must be above entry ({entry_cents})")

    return target_cents


def calculate_ratchet_stop(high_cents: int, mode: str, value: float, atr: float, current_stop_cents: int) -> int:
    """Calculate trailing ratchet stop. Only ratchets UP, never down."""
    _validate_mode(mode)

    if mode == "atr":
        _validate_atr(atr)
        atr_cents = round(atr * 100)
        new_stop = high_cents - round(value * atr_cents)
    elif mode == "dollar":
        new_stop = high_cents - round(value * 100)
    elif mode == "percent":
        new_stop = round(high_cents * (1 - value / 100))

    if new_stop <= 0:
        return current_stop_cents

    # Only ratchet UP
    return max(new_stop, current_stop_cents)


def validate_target_ordering(t1_cents: int, t2_cents: int, t3_cents: int, entry_cents: int):
    """Validate t1 < t2 < t3 and all above entry (SEC-3)."""
    if t1_cents <= entry_cents:
        raise ValueError(f"T1 ({t1_cents}) must be above entry ({entry_cents})")
    if t2_cents <= t1_cents:
        raise ValueError(f"T2 ({t2_cents}) must be above T1 ({t1_cents})")
    if t3_cents <= t2_cents:
        raise ValueError(f"T3 ({t3_cents}) must be above T2 ({t2_cents})")
