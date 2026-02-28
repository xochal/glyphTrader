"""
Centralized config loader: deep-merge JSON defaults with SQLite overrides.
Single-worker cache with dirty flag.
"""

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from db.database import get_db

logger = logging.getLogger("glyphTrader.config_loader")

_CONFIG_DIR = Path(__file__).parent

_cached_params: Optional[Dict] = None
_cached_watchlist: Optional[Dict] = None
_cache_dirty = True


def _load_json_defaults(filename: str) -> Dict:
    path = _CONFIG_DIR / filename
    with open(path) as f:
        return json.load(f)


def _fetch_overrides(config_type: str) -> Dict[str, any]:
    """Fetch all overrides for a config_type from DB."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM trading_config WHERE config_type = ?",
                (config_type,),
            ).fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}
    except Exception as e:
        logger.warning(f"Failed to fetch overrides for {config_type}: {e}")
        return {}


def get_trading_params() -> Dict:
    """Load trading params: JSON defaults deep-merged with DB overrides. Cached."""
    global _cached_params, _cache_dirty
    if _cached_params is not None and not _cache_dirty:
        return _cached_params

    defaults = _load_json_defaults("trading_params.json")
    merged = copy.deepcopy(defaults)
    overrides = _fetch_overrides("trading_params")

    for section_key, override_val in overrides.items():
        if section_key in merged and isinstance(merged[section_key], dict) and isinstance(override_val, dict):
            merged[section_key].update(override_val)
        else:
            merged[section_key] = override_val

    _cached_params = merged
    _cache_dirty = False
    return merged


def get_watchlist() -> Dict:
    """Load watchlist: JSON defaults with delta-based overrides applied. Cached."""
    global _cached_watchlist, _cache_dirty
    if _cached_watchlist is not None and not _cache_dirty:
        return _cached_watchlist

    defaults = _load_json_defaults("watchlist.json")
    merged = copy.deepcopy(defaults)
    overrides = _fetch_overrides("watchlist")

    # Apply modifications to existing stocks
    modifications = overrides.get("watchlist_modify", {})
    for symbol, changes in modifications.items():
        for stock in merged["stocks"]:
            if stock["symbol"] == symbol:
                stock.update(changes)
                break

    # Remove stocks
    removals = overrides.get("watchlist_remove", [])
    if removals:
        merged["stocks"] = [s for s in merged["stocks"] if s["symbol"] not in removals]

    # Add new stocks
    additions = overrides.get("watchlist_add", [])
    existing_symbols = {s["symbol"] for s in merged["stocks"]}
    for stock in additions:
        if stock["symbol"] not in existing_symbols:
            merged["stocks"].append(stock)

    _cached_watchlist = merged
    return merged


def invalidate_cache():
    """Mark cache as dirty. Next get_* call will reload from DB."""
    global _cache_dirty, _cached_params, _cached_watchlist
    _cache_dirty = True
    _cached_params = None
    _cached_watchlist = None


def get_defaults() -> Dict:
    """Raw JSON defaults only (for UI display)."""
    return {
        "trading_params": _load_json_defaults("trading_params.json"),
        "watchlist": _load_json_defaults("watchlist.json"),
    }


def get_overrides() -> Dict:
    """Raw DB overrides only (for UI modified indicators)."""
    return {
        "trading_params": _fetch_overrides("trading_params"),
        "watchlist": _fetch_overrides("watchlist"),
    }


def save_override(key: str, value, config_type: str):
    """Save a single override to DB."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO trading_config (key, value, config_type, updated_at) VALUES (?, ?, ?, ?)",
            (key, json.dumps(value), config_type, now),
        )
    invalidate_cache()


def delete_override(key: str, config_type: str):
    """Delete a single override from DB."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM trading_config WHERE key = ? AND config_type = ?",
            (key, config_type),
        )
    invalidate_cache()


def reset_all_overrides():
    """Delete ALL overrides from DB."""
    with get_db() as conn:
        conn.execute("DELETE FROM trading_config")
    invalidate_cache()


def reset_section(section: str):
    """Delete overrides for a specific section."""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM trading_config WHERE key = ? AND config_type = 'trading_params'",
            (section,),
        )
    invalidate_cache()
