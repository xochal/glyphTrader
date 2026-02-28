"""
SQLite database connection management.
WAL mode, busy timeout, foreign keys enforced on every connection.
"""

import os
import re
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("glyphTrader.db")

DB_PATH = os.environ.get("DB_PATH", "/app/data/trading.db")
SCHEMA_VERSION = 5


def get_version() -> str:
    # Try Docker path first, then development path
    version_path = Path("/app/VERSION")
    if not version_path.exists():
        version_path = Path(__file__).parent.parent.parent / "VERSION"
    try:
        raw = version_path.read_text().strip()
        if re.match(r"^\d+\.\d+\.\d+$", raw):
            return raw
    except Exception:
        pass
    return "0.0.0-unknown"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current = row["version"] if row else 0

        if current < 1:
            _apply_v1(conn)
        if current < 2:
            _apply_v2(conn)
        if current < 3:
            _apply_v3(conn)
        if current < 4:
            _apply_v4(conn)
        if current < 5:
            _apply_v5(conn)

        if current == 0:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        elif current < SCHEMA_VERSION:
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

        logger.info(f"Database initialized at {DB_PATH} (schema v{SCHEMA_VERSION})")


def _apply_v1(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            encrypted INTEGER DEFAULT 0,
            key_version INTEGER DEFAULT 1,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            is_revoked INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT DEFAULT 'long',
            entry_price_cents INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            shares INTEGER NOT NULL,
            shares_remaining INTEGER NOT NULL,
            stop_price_cents INTEGER NOT NULL,
            base_stop_cents INTEGER NOT NULL,
            target_t1_price_cents INTEGER NOT NULL,
            target_t2_price_cents INTEGER NOT NULL,
            target_t3_price_cents INTEGER NOT NULL,
            t1_filled INTEGER DEFAULT 0,
            t1_filled_price_cents INTEGER,
            t1_filled_time TEXT,
            t1_shares INTEGER DEFAULT 0,
            t2_filled INTEGER DEFAULT 0,
            t2_filled_price_cents INTEGER,
            t2_filled_time TEXT,
            t2_shares INTEGER DEFAULT 0,
            t3_filled INTEGER DEFAULT 0,
            t3_filled_price_cents INTEGER,
            t3_filled_time TEXT,
            t3_shares INTEGER DEFAULT 0,
            stop_filled INTEGER DEFAULT 0,
            stop_filled_price_cents INTEGER,
            stop_filled_time TEXT,
            stop_shares INTEGER DEFAULT 0,
            original_atr_cents INTEGER NOT NULL,
            last_stepped_stop_date TEXT,
            realized_pnl_cents INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open',
            position_state TEXT DEFAULT 'ENTRY_PENDING',
            exit_reason TEXT,
            pyramid_count INTEGER DEFAULT 0,
            blended_entry_price_cents INTEGER,
            close_time TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            v4_score REAL,
            action TEXT NOT NULL,
            skip_reason TEXT,
            entry_price_cents INTEGER,
            stop_price_cents INTEGER,
            t1_price_cents INTEGER,
            t2_price_cents INTEGER,
            t3_price_cents INTEGER,
            shares INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            account_value_cents INTEGER,
            cash_cents INTEGER,
            positions_value_cents INTEGER,
            daily_pnl_cents INTEGER,
            spy_close_cents INTEGER,
            qqq_close_cents INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS regime_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            vix_level REAL,
            spy_above_sma100 INTEGER DEFAULT 0,
            regime_allows_entry INTEGER DEFAULT 1,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS order_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            order_type TEXT NOT NULL,
            shares INTEGER NOT NULL,
            price_cents INTEGER,
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT,
            success INTEGER DEFAULT 0,
            attempted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ip_address TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        );

        INSERT OR IGNORE INTO regime_state (id, vix_level, spy_above_sma100, regime_allows_entry)
        VALUES (1, 20.0, 1, 1);
    """)


def _apply_v2(conn: sqlite3.Connection):
    # Add qqq_above_sma100 column to regime_state
    cols = [row[1] for row in conn.execute("PRAGMA table_info(regime_state)").fetchall()]
    if "qqq_above_sma100" not in cols:
        conn.execute("ALTER TABLE regime_state ADD COLUMN qqq_above_sma100 INTEGER DEFAULT 0")


def _apply_v3(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trading_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            config_type TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trading_config_type ON trading_config(config_type);
    """)


def _apply_v4(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'tradier',
            PRIMARY KEY (symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_candles_symbol ON candles(symbol);
        CREATE INDEX IF NOT EXISTS idx_candles_date ON candles(date);
    """)


def _apply_v5(conn: sqlite3.Connection):
    """Schema v5: manual trade management columns, dismissed_orphans table."""
    # New columns on trades table (individual ALTERs — safe if column already exists)
    new_columns = [
        ("trade_type", "TEXT NOT NULL DEFAULT 'auto'"),
        ("stop_mode", "TEXT"),
        ("stop_mode_value", "REAL"),
        ("ratchet_enabled", "INTEGER DEFAULT 0"),
        ("ratchet_mode", "TEXT"),
        ("ratchet_value", "REAL"),
        ("ratchet_high_cents", "INTEGER"),
        ("t1_mode", "TEXT"),
        ("t1_mode_value", "REAL"),
        ("t2_mode", "TEXT"),
        ("t2_mode_value", "REAL"),
        ("t3_mode", "TEXT"),
        ("t3_mode_value", "REAL"),
        ("targets_enabled", "INTEGER DEFAULT 1"),
        ("t1_exit_pct", "INTEGER"),
        ("t2_exit_pct", "INTEGER"),
        ("t3_exit_pct", "INTEGER"),
    ]
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    for col_name, col_def in new_columns:
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_def}")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dismissed_orphans (
            symbol TEXT PRIMARY KEY,
            dismissed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trades_trade_type ON trades(trade_type);
    """)
