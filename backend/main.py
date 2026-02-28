"""
glyphTrader — FastAPI application entry point.
Single worker (--workers 1), APScheduler, startup reconciliation.
"""

import os
import secrets
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import setup_logging
from db.database import init_db, get_db, get_version

logger = logging.getLogger("glyphTrader")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    setup_logging()
    logger.info(f"glyphTrader v{get_version()} starting...")

    # Initialize database
    init_db()

    # License version invalidation: if VERSION changed, clear license and force sandbox
    try:
        from license import is_production_licensed, clear_license
        from api.auth import _get_setting, _set_setting
        with get_db() as conn:
            stored_ver = _get_setting(conn, "license_version")
            if stored_ver and stored_ver != get_version():
                clear_license()
                _set_setting(conn, "tradier_environment", "sandbox")
                logger.warning(
                    f"Version changed ({stored_ver} -> {get_version()}): "
                    "license cleared, environment forced to sandbox"
                )
    except Exception as e:
        logger.debug(f"License version check skipped: {e}")

    # First-boot setup token
    with get_db() as conn:
        setup_complete = conn.execute(
            "SELECT value FROM settings WHERE key = 'setup_complete'"
        ).fetchone()
        if not setup_complete or setup_complete["value"] != "true":
            # Check for ADMIN_PASSWORD env var
            admin_pw = os.environ.get("ADMIN_PASSWORD")
            if admin_pw:
                _auto_setup(conn, admin_pw)
                # Clear from environment (defense in depth)
                os.environ.pop("ADMIN_PASSWORD", None)
            else:
                token = f"setup-{secrets.token_hex(6)}"
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    ("setup_token", token),
                )
                logger.info(f"\n{'='*50}")
                logger.info(f"  SETUP TOKEN: {token}")
                logger.info(f"  Open the web UI and enter this token to set your password")
                logger.info(f"{'='*50}\n")

    # Startup reconciliation
    try:
        from tradier.reconciliation import run_startup_reconciliation
        run_startup_reconciliation()
    except Exception as e:
        logger.warning(f"Startup reconciliation skipped: {e}")

    # Startup data fetch — DataStore is volatile (in-memory), so populate it
    # immediately rather than waiting for the 12:30 PM ET scheduled job
    try:
        from scheduler.jobs import job_fetch_daily_data
        logger.info("Running startup data fetch (DataStore is empty after restart)...")
        job_fetch_daily_data()
    except Exception as e:
        logger.warning(f"Startup data fetch skipped: {e}")

    # Update regime state from freshly enriched DataStore
    try:
        from data.datastore import DataStore
        from config.config_loader import get_trading_params
        from api.auth import _set_setting, _get_setting
        store = DataStore()
        vix_level = store.get_vix_level()
        spy_above = 1 if store.get_spy_sma100() else 0
        qqq_above = 1 if store.get_qqq_sma100() else 0
        tp = get_trading_params()
        vix_max = tp["filters"]["regime_vix_max"]
        vix_allow_below = tp["filters"]["regime_vix_allow_below"]
        index_ok = spy_above or qqq_above
        allows = 1 if vix_level and vix_level < vix_max and (index_ok or vix_level < vix_allow_below) else 0
        from datetime import datetime, timezone as tz
        now = datetime.now(tz.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                "UPDATE regime_state SET vix_level = ?, spy_above_sma100 = ?, "
                "qqq_above_sma100 = ?, regime_allows_entry = ?, updated_at = ? WHERE id = 1",
                (vix_level, spy_above, qqq_above, allows, now),
            )
        logger.info(f"Regime updated: VIX={vix_level}, SPY>SMA100={bool(spy_above)}, QQQ>SMA100={bool(qqq_above)}, allows={bool(allows)}")
    except Exception as e:
        logger.warning(f"Startup regime update skipped: {e}")

    # Start scheduler
    from scheduler.jobs import start_scheduler
    start_scheduler()

    yield

    # Shutdown
    from scheduler.jobs import scheduler
    scheduler.shutdown(wait=False)
    logger.info("glyphTrader stopped")


def _auto_setup(conn, admin_pw: str):
    """Auto-setup from ADMIN_PASSWORD env var."""
    import bcrypt
    from db import crypto
    from api.auth import _set_setting

    pw_hash = bcrypt.hashpw(admin_pw.encode(), bcrypt.gensalt()).decode()
    salt = crypto.generate_salt()
    jwt_secret = secrets.token_hex(32)
    recovery_key = secrets.token_hex(32)
    recovery_hash = bcrypt.hashpw(recovery_key.encode(), bcrypt.gensalt()).decode()

    _set_setting(conn, "admin_password_hash", pw_hash)
    _set_setting(conn, "fernet_salt", salt.hex())
    _set_setting(conn, "jwt_secret", jwt_secret)
    _set_setting(conn, "recovery_key_hash", recovery_hash)
    _set_setting(conn, "trading_enabled", "false")
    _set_setting(conn, "observe_only", "false")
    _set_setting(conn, "tradier_environment", "sandbox")
    _set_setting(conn, "setup_complete", "true")

    logger.info("Auto-setup from ADMIN_PASSWORD complete")
    logger.info(f"RECOVERY KEY (save this!): {recovery_key}")
    logger.warning("Remove ADMIN_PASSWORD from .env after first boot")


app = FastAPI(
    title="glyphTrader",
    version=get_version(),
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# CORS — strict origin
allowed_origins = os.environ.get("CORS_ORIGINS", "").split(",")
allowed_origins = [o.strip() for o in allowed_origins if o.strip()]
if not allowed_origins:
    allowed_origins = ["https://localhost", "https://127.0.0.1"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "X-CSRF-Token", "Content-Type"],
)

# Register routes
from api.routes_health import router as health_router
from api.auth import router as auth_router
from api.routes_dashboard import router as dashboard_router
from api.routes_trades import router as trades_router
from api.routes_settings import router as settings_router
from api.routes_charts import router as charts_router
from api.routes_trade_settings import router as trade_settings_router
from api.routes_manual_trades import router as manual_trades_router

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(trades_router)
app.include_router(settings_router)
app.include_router(charts_router)
app.include_router(trade_settings_router)
app.include_router(manual_trades_router)
