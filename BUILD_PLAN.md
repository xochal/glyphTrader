# glyphTrader — Standalone Trading System

## Project Overview

**glyphTrader** is a slim, self-contained, Dockerized trading system that runs the TFT V4/V7 strategy exclusively through Tradier. Single server, no backtest, no Alpaca, no Discord. Designed to be portable — `git clone` + `docker compose up -d` on any Linux machine (including Raspberry Pi 5).

*A glyph is an ancient symbol of power — the algorithm is the glyph, carved once and executed mechanically. The camelCase `T` is a nod to the programmers who built it.*

**Program Name**: glyphTrader
**Repository**: `glyphTrader`
**Planning Doc**: `BUILD_PLAN.md`
**Status**: PLANNING (not started)
**Target**: New dedicated machine (prototype on current server first)

---

## Architecture

```
glyphTrader/
├── setup.sh                    # One-command install: port check + docker compose + print URL/token
├── update.sh                   # Git pull + rebuild + restart + health check
├── VERSION                     # Semantic version string (e.g., "1.0.0"), read by backend at startup
├── .gitignore                  # Excludes .env, data/, *.db, node_modules, frontend/build, __pycache__
├── docker-compose.yml          # 3 containers
├── .env.example                # Template: TFT_HTTPS_PORT, TFT_HTTP_PORT, ADMIN_PASSWORD (optional)
├── Caddyfile                   # HTTPS reverse proxy config + security headers
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt        # Pinned with SHA-256 hashes (pip-compile --generate-hashes)
│   ├── main.py                 # FastAPI app + APScheduler + startup reconciliation
│   ├── config/
│   │   ├── watchlist.json      # Hardcoded stock universe + sector thresholds + index mapping + tier multipliers
│   │   └── trading_params.json # ATR targets, sizing, filters, time stops, pyramid limits (hardcoded)
│   ├── core/
│   │   ├── scoring.py          # V4 scoring engine (pure pandas/numpy, no SQL, verbatim constants)
│   │   ├── filters.py          # EMA, regime, price movement, slippage, sector, V4 (pure DataFrame ops)
│   │   ├── indicators.py       # SMA, EMA, RSI, ADX, ATR, Stochastic, Bollinger, LINEAR_REG_SLOPE
│   │   ├── plan_generator.py   # Daily signal generation (reads from DataStore, sorts by V4 desc)
│   │   ├── position_sizer.py   # Cash-based sizing + VIX dynamic + sector tier multipliers + whole shares
│   │   └── regime.py           # Regime detector (VIX level + SPY SMA100, stock-to-index from config)
│   ├── tradier/
│   │   ├── client.py           # Tradier API wrapper (orders + market data + history) — custom __repr__ redacts auth
│   │   ├── order_manager.py    # Order placement, OCO, cascades, cancel-all pattern
│   │   ├── execution.py        # Daily trade execution + time stops + signal priority sort (V4 desc)
│   │   ├── safety_monitor.py   # Order structure enforcement + fill detection (combined)
│   │   ├── stepped_stops.py    # Stepped stop ratcheting (reads ATR from DataStore)
│   │   └── reconciliation.py   # Ghost position detection + startup state recovery
│   ├── data/
│   │   ├── market_data.py      # Tradier historical bars + quotes + VIX
│   │   ├── enrichment.py       # Calculate ALL indicators from raw bars (pandas/numpy/scipy)
│   │   ├── datastore.py        # In-memory DataStore: holds enriched DataFrames, provides lookups
│   │   └── benchmark.py        # SPY/QQQ daily data for comparison charts
│   ├── api/
│   │   ├── auth.py             # JWT auth, login, refresh (with rotation), session management, server-side timeout
│   │   ├── middleware.py       # Auth middleware, rate limiting, CORS, credential redaction
│   │   ├── routes_dashboard.py # Positions, P&L, account stats
│   │   ├── routes_trades.py    # Trade history, closed trades
│   │   ├── routes_settings.py  # Credentials (encrypted), kill switch (requires re-auth)
│   │   ├── routes_charts.py    # Equity curve, benchmark data
│   │   └── routes_health.py    # /health endpoint (boolean only, no internal state leakage)
│   ├── db/
│   │   ├── database.py         # SQLite connection (WAL mode, busy_timeout, aiosqlite)
│   │   ├── models.py           # All table definitions (integer cents for money, ISO strings for dates)
│   │   └── crypto.py           # Fernet encryption — key derived from admin password via Argon2id
│   └── scheduler/
│       └── jobs.py             # APScheduler: coalesce=True, max_instances=1, misfire_grace_time
├── frontend/
│   ├── Dockerfile              # GENERATE_SOURCEMAP=false in build stage
│   ├── package.json            # package-lock.json committed, use npm ci
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── Login.tsx       # Login + first-boot setup (requires setup token)
│   │   │   ├── Dashboard.tsx   # Positions, equity curve, account stats, kill switch
│   │   │   ├── TradeHistory.tsx # Closed trades, win rate, monthly heatmap
│   │   │   └── Settings.tsx    # API keys, kill switch (re-auth required), system status
│   │   ├── components/
│   │   │   ├── EquityCurve.tsx  # Account vs SPY vs QQQ (Recharts)
│   │   │   ├── PositionTable.tsx
│   │   │   ├── TradeTable.tsx
│   │   │   ├── MonthlyHeatmap.tsx
│   │   │   ├── AccountStats.tsx
│   │   │   └── KillSwitch.tsx   # Requires password re-entry + fruit confirmation
│   │   └── services/
│   │       ├── api.ts           # Axios with JWT interceptor + auto-refresh
│   │       └── auth.ts          # Login/logout/token refresh + 30-min idle detection
│   └── nginx.conf               # Serve static build, block .map files
└── data/                        # Docker volume mount point (700 permissions on directory)
    └── trading.db               # SQLite database (persistent, permissions 600)
```

### Container Stack

| Container | Image | Purpose | Port |
|-----------|-------|---------|------|
| `caddy` | `caddy:2.7-alpine` (pinned to SHA digest) | HTTPS reverse proxy, auto TLS, security headers | configurable (default 443/80) |
| `backend` | Custom (`python:3.11.8-slim-bookworm`, pinned to SHA digest) | FastAPI + APScheduler + SQLite | 8000 (internal only) |
| `frontend` | Custom (`nginx:1.25-alpine`, pinned to SHA digest) | Static React build | 80 (internal only) |

**CRITICAL**: Backend and frontend containers do NOT publish ports to the host. Only Caddy's ports are exposed. Inter-container communication uses Docker's internal network via service names.

All containers:
- Run as non-root user
- Use `read_only: true` filesystem with `tmpfs` for `/tmp`
- Health checks defined in docker-compose.yml
- Core dumps disabled (`ulimit -c 0`)
- Images pinned to SHA-256 digests (not just tags) for supply chain security

Caddy routes:
- `https://host/api/*` → backend:8000
- `https://host/*` → frontend:80

### Port Configuration

The only ports exposed to the host are Caddy's HTTPS (default 443) and HTTP redirect (default 80). These are configurable via `.env` file to avoid conflicts with other services on the same machine:

```env
# .env (create alongside docker-compose.yml)
TFT_HTTPS_PORT=443    # Change if 443 is in use (e.g., 8443)
TFT_HTTP_PORT=80      # Change if 80 is in use (e.g., 8080)
```

The `docker-compose.yml` references these with defaults:
```yaml
services:
  caddy:
    ports:
      - "${TFT_HTTPS_PORT:-443}:443"
      - "${TFT_HTTP_PORT:-80}:80"
```

Internal container ports (backend 8000, frontend 80) are never exposed to the host — they only exist on Docker's internal bridge network. These never conflict with anything.

### One-Command Install: `setup.sh`

The entire installation and first-run experience is a single command:

```bash
git clone <repo-url> glyphTrader && cd glyphTrader && ./setup.sh
```

`setup.sh` is a ~40-line bash script (no dependencies beyond `ss`, which ships with every Linux distro) that handles everything automatically:

**First run behavior:**
```
$ ./setup.sh

glyphTrader
===========

Checking ports...
  Port 443: in use (nginx)
  Port 80:  in use (nginx)
  Trying 8443/8080... available ✓
  → Saved to .env

Starting containers...
  ✓ caddy
  ✓ backend
  ✓ frontend

Waiting for backend...

=========================================
  Setup Token: setup-a7f3b2e9c1d4

  Open: https://localhost:8443
  Enter the token above to set your password
=========================================
```

**What setup.sh does step by step:**
1. Checks if `.env` exists — if not, creates from `.env.example`
2. Reads `TFT_HTTPS_PORT` and `TFT_HTTP_PORT` from `.env` (defaults: 443/80)
3. Checks if those ports are free on the host using `ss -tlnp`
4. If occupied: tries fallback pairs (8443/8080 → 9443/9080 → 10443/10080) until it finds a free pair
5. Writes the chosen ports to `.env`
6. Runs `docker compose up -d`
7. Waits for the backend health check to pass (polls `/api/health` via the internal Docker network, max 30 seconds)
8. Detects if this is first run (`setup_complete` not set) or a restart
9. **First run**: extracts the setup token from `docker compose logs backend` and prints it with the access URL
10. **Restart**: prints just the access URL and "System ready"
11. Detects the machine's LAN IP automatically (`hostname -I`) for the URL

**Subsequent runs:**
```
$ ./setup.sh

glyphTrader
===========

Checking ports...
  Port 8443: available ✓ (from .env)
  Port 8080: available ✓ (from .env)

Starting containers...
  ✓ caddy (already running)
  ✓ backend (already running)
  ✓ frontend (already running)

System ready: https://localhost:8443
```

`docker compose up -d` also still works directly for restarts — `setup.sh` is just the friendly wrapper that handles port detection and prints the URL.

**Manual override**: If the user wants specific ports, they edit `.env` before running `setup.sh` (or pass them as args: `./setup.sh --https-port 9443 --http-port 9080`). The script respects existing `.env` values and only auto-detects if the configured ports are occupied.

#### Docker Prerequisite Check

`setup.sh` checks for Docker and Docker Compose before doing anything else:

```
$ ./setup.sh

glyphTrader
===========

Checking prerequisites...
  Docker:         ✓ 24.0.7
  Docker Compose: ✓ 2.23.0

Checking ports...
  ...
```

**If Docker is missing**, the script offers to install it:
```
Checking prerequisites...
  Docker: NOT FOUND

Docker is required but not installed.
Install Docker now? [Y/n] y
  → Installing via get.docker.com...
  → Adding current user to 'docker' group...
  → Docker 24.0.7 installed ✓

NOTE: You may need to log out and back in for group changes to take effect.
      Then re-run: ./setup.sh
```

The install uses Docker's official convenience script (`get.docker.com`) which works across Debian, Ubuntu, Fedora, CentOS, and Raspberry Pi OS. If the user declines or the install fails, `setup.sh` exits with a clear message.

**Additional checks** (warnings, not blockers):
- **Architecture**: `uname -m` — logs `aarch64` (Pi/ARM) or `x86_64`. No code changes needed (multi-arch Docker images handle this)
- **Disk space**: `df -h .` — warns if < 2 GB free
- **RAM**: reads `/proc/meminfo` — warns if < 1 GB total (Pi 4 2GB is minimum recommended, Pi 5 8GB preferred)

### Software Update: `update.sh`

Updates the system from the git repository. Safe and non-destructive — preserves all user data:

```bash
./update.sh
```

**What update.sh does step by step:**
1. Checks that we're in a git repository
2. Reads current version from `VERSION` file
3. `git fetch origin main` — download latest without applying
4. Compares local vs remote: `git rev-list HEAD..origin/main --count`
5. If no new commits: prints "Already up to date (v1.2.3)" and exits
6. Shows a summary of what changed: `git log HEAD..origin/main --oneline`
7. `git pull origin main` — applies the update
8. Reads new version from `VERSION` file
9. `docker compose build` — rebuilds containers with new code
10. `docker compose up -d` — restarts with new images
11. Waits for backend health check (polls `/api/health`, max 30 seconds)
12. Prints result:

```
$ ./update.sh

glyphTrader — Update
====================

Current version: v1.2.3
Checking for updates...

3 new commits:
  a1b2c3d Fix stepped stop edge case with low ATR
  d4e5f6g Add position count to dashboard
  h7i8j9k Update Tradier client for API v2

Pulling updates...  ✓
Building containers...  ✓
Restarting...  ✓
Health check...  ✓

Updated: v1.2.3 → v1.3.0
System ready: https://localhost:8443
```

**What's preserved across updates** (never in git, never overwritten):
- `data/trading.db` (all trade history, settings, encrypted credentials)
- `data/trading.db-wal`, `data/trading.db-shm` (SQLite WAL files)
- `.env` (port config, optional admin password)
- `data/backups/` (SQLite daily backups)

**What gets updated** (all in git):
- All Python code (backend/)
- All React code (frontend/)
- Docker configuration (Dockerfile, docker-compose.yml, Caddyfile)
- Config files (watchlist.json, trading_params.json)
- VERSION file

**Database migrations**: If an update includes schema changes, the backend handles this at startup. `database.py` reads the current schema version from a `schema_version` table and applies any pending migrations sequentially. Migrations are idempotent (use `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN IF NOT EXISTS` patterns). This means `update.sh` doesn't need to know about database changes — the backend handles it automatically on restart.

**Edge cases**:
- User has local modifications: `git pull` will fail. `update.sh` detects this, prints "You have local modifications. Stash or discard them first." and exits without changing anything
- Update fails mid-build: old containers are still running (Docker Compose only swaps on `up -d`). The system remains on the previous version. User can re-run `update.sh`
- Network failure during `git fetch`: script detects and exits cleanly

**Settings page integration**: The Settings page shows the current version (read from `VERSION` file at backend startup) and a "Check for Updates" indicator. The check is **read-only** — it calls a backend endpoint that runs `git fetch --dry-run` and compares refs. Actual updates are always done via SSH + `./update.sh` for safety (never auto-update, never update from the web UI).

### Git Repository Setup

glyphTrader lives in its own git repository. This keeps the repo small, deployable, and portable.

**Repo name**: `glyphTrader`

### .gitignore

```gitignore
# User data (NEVER commit)
.env
data/
*.db
*.db-wal
*.db-shm

# Claude Code (project-level config/memory — NEVER commit)
.claude/

# Python
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/

# Node
node_modules/
frontend/build/
frontend/.env*

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Docker build artifacts
*.tar

# Logs
*.log
```

**Files that ARE committed** (and thus updated by `update.sh`):
- `setup.sh`, `update.sh`, `VERSION`
- `docker-compose.yml`, `Caddyfile`, `.env.example`
- `backend/` (all Python code, Dockerfile, requirements.txt, config/*.json)
- `frontend/` (all React code, Dockerfile, package.json, package-lock.json, nginx.conf)
- `README.md` (short: clone, setup.sh, open URL)

---

## Database Schema (SQLite)

**SQLite Configuration** (set on every connection):
```sql
PRAGMA journal_mode=WAL;          -- Allow concurrent readers with single writer
PRAGMA busy_timeout=5000;         -- Retry on lock instead of failing
PRAGMA foreign_keys=ON;           -- Enforce referential integrity
```

**Money Storage**: All monetary values stored as INTEGER (cents) to avoid floating-point drift. Convert to dollars only at API response boundary. This replaces the current system's Decimal/Numeric types which are PostgreSQL-specific.

**DateTime Storage**: All timestamps stored as ISO 8601 TEXT strings (e.g., `2026-02-25T13:00:00-05:00`). Application code handles timezone conversion (America/New_York).

### `settings`
| Column | Type | Description |
|--------|------|-------------|
| key | TEXT PK | Setting name |
| value | TEXT | Encrypted for sensitive values |
| encrypted | INTEGER | 1 if value is Fernet-encrypted, 0 otherwise |
| key_version | INTEGER | Encryption key version (incremented on password change) |
| updated_at | TEXT | ISO datetime of last modification |

Settings stored:
- `tradier_api_token` (encrypted) — Tradier API bearer token
- `tradier_environment` — enum: `"sandbox"` or `"production"` (NOT a URL — see Security)
- `trading_enabled` — kill switch (`"true"` / `"false"`)
- `admin_password_hash` — bcrypt hash
- `jwt_secret` — random hex, generated at first boot, never hardcoded
- `fernet_salt` — random salt for Argon2 key derivation
- `setup_complete` — `"true"` after first-boot setup (disables setup endpoint permanently)
- `last_activity` — ISO datetime for server-side session timeout
- `degraded_token` (encrypted with jwt_secret) — second copy of Tradier token for safety monitor when system is "locked"

### `refresh_tokens`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| token_hash | TEXT UNIQUE | SHA-256 hash of refresh token |
| expires_at | TEXT | ISO datetime |
| is_revoked | INTEGER | 1 if revoked, 0 if active |
| created_at | TEXT | ISO datetime |

### `trades`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| symbol | TEXT | Stock symbol (validated: `^[A-Z]{1,5}$`) |
| direction | TEXT | long |
| entry_price_cents | INTEGER | Actual fill price × 100 |
| entry_time | TEXT | ISO datetime of fill |
| shares | INTEGER | Total shares entered (whole shares only) |
| shares_remaining | INTEGER | Shares still open |
| stop_loss_price_cents | INTEGER | Current stop level × 100 |
| base_stop_cents | INTEGER | Original stop at entry × 100 (for stepped calc — UPDATED on breakeven) |
| target_t1_price_cents | INTEGER | T1 target × 100 |
| target_t2_price_cents | INTEGER | T2 target × 100 |
| target_t3_price_cents | INTEGER | T3 target × 100 |
| t1_filled | INTEGER | 0/1 |
| t1_filled_price_cents | INTEGER | Actual T1 fill price × 100 |
| t1_filled_time | TEXT | ISO datetime |
| t1_shares | INTEGER | Shares exited at T1 |
| t2_filled | INTEGER | 0/1 |
| t2_filled_price_cents | INTEGER | Actual T2 fill price × 100 |
| t2_filled_time | TEXT | ISO datetime |
| t2_shares | INTEGER | Shares exited at T2 |
| t3_filled | INTEGER | 0/1 |
| t3_filled_price_cents | INTEGER | Actual T3 fill price × 100 |
| t3_filled_time | TEXT | ISO datetime |
| t3_shares | INTEGER | Shares exited at T3 |
| stop_filled | INTEGER | 0/1 |
| stop_filled_price_cents | INTEGER | Stop fill price × 100 |
| stop_filled_time | TEXT | ISO datetime |
| stop_shares | INTEGER | Shares exited at stop |
| original_atr_cents | INTEGER | ATR at entry × 100 (for stepped stops) |
| last_stepped_stop_date | TEXT | Date of last stepped stop ratchet |
| realized_pnl_cents | INTEGER | Closed P&L × 100 |
| status | TEXT | open/closed |
| position_state | TEXT | State machine state |
| exit_reason | TEXT | stop/t1/t2/t3/time_stop/manual |
| pyramid_count | INTEGER | Number of pyramid adds (0 = initial entry, max 2) |
| blended_entry_price_cents | INTEGER | Weighted avg entry after pyramids × 100 |
| close_time | TEXT | ISO datetime when fully closed |
| created_at | TEXT | ISO datetime |
| updated_at | TEXT | ISO datetime |

### `daily_plans`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| date | TEXT | Date string YYYY-MM-DD |
| symbol | TEXT | |
| v4_score | REAL | Signal score (OK as float — display only) |
| action | TEXT | buy/skip |
| skip_reason | TEXT | Which filter blocked |
| entry_price_cents | INTEGER | Planned entry × 100 |
| stop_price_cents | INTEGER | Planned stop × 100 |
| t1_price_cents | INTEGER | Planned T1 × 100 |
| t2_price_cents | INTEGER | Planned T2 × 100 |
| t3_price_cents | INTEGER | Planned T3 × 100 |
| shares | INTEGER | Planned shares |
| created_at | TEXT | ISO datetime |

### `portfolio_snapshots`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| date | TEXT | Date string YYYY-MM-DD |
| account_value_cents | INTEGER | Total equity × 100 |
| cash_cents | INTEGER | Available cash × 100 |
| positions_value_cents | INTEGER | Invested value × 100 |
| daily_pnl_cents | INTEGER | Day's P&L × 100 |
| spy_close_cents | INTEGER | SPY price × 100 |
| qqq_close_cents | INTEGER | QQQ price × 100 |
| created_at | TEXT | ISO datetime |

### `regime_state`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Always 1 (singleton) |
| vix_level | REAL | Current VIX (OK as float — not money) |
| spy_above_sma100 | INTEGER | 0/1 |
| regime_allows_entry | INTEGER | 0/1 computed |
| updated_at | TEXT | ISO datetime |

### `order_state`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| trade_id | INTEGER FK | Links to trades |
| order_id | TEXT | Tradier order ID |
| order_type | TEXT | oco/stop/limit |
| shares | INTEGER | Order quantity |
| price_cents | INTEGER | Order price × 100 |
| status | TEXT | open/filled/cancelled |
| created_at | TEXT | ISO datetime |
| updated_at | TEXT | ISO datetime |

### `login_attempts`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| ip_address | TEXT | Source IP |
| success | INTEGER | 0/1 |
| attempted_at | TEXT | ISO datetime |

### `audit_log`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| event_type | TEXT | login_success/login_failure/token_change/env_change/kill_switch/session_revoke/password_change |
| ip_address | TEXT | Source IP |
| details | TEXT | JSON with event-specific data (NEVER contains credentials) |
| created_at | TEXT | ISO datetime |

Audit log is **append-only** — no DELETE endpoint exposed. Retained indefinitely.

---

## Security Design

### First-Boot Setup (Race Condition Protected)
1. On first container start, backend detects no admin account exists
2. Generates a **one-time setup token** and prints it to stdout (visible only via `docker compose logs backend`)
3. The `/api/auth/setup` endpoint requires this token + desired password (username is always "admin")
4. After setup completes: `setup_complete` is set to `"true"` — the endpoint returns 403 permanently. A **recovery key** (32-byte hex) is returned once and must be saved offline
5. Alternative: password can be set via `ADMIN_PASSWORD` in the `.env` file (chmod 600, NOT in docker-compose.yml directly). After first boot, the env var is consumed and the system logs a warning if it's still set after `setup_complete=true`

### Single Admin User
There is exactly one user account: `admin`. The username is hardcoded — there is no username field on the login page, no user management, no registration. The login page is a single password field. This keeps things simple and eliminates username enumeration.

### Authentication Flow
1. **Login**: POST `/api/auth/login` with password only (username is always "admin") → returns JWT access token (15 min) + refresh token (24-hour, httpOnly cookie with `SameSite=Strict; Secure`)
2. **All API routes**: Require valid JWT in `Authorization: Bearer` header (except `/api/auth/login`, `/api/auth/refresh`, `/api/health`)
3. **Token refresh with rotation**: POST `/api/auth/refresh` with httpOnly cookie + `X-CSRF-Token` header → check `refresh_tokens` table for revocation → check server-side `last_activity` (30 min timeout) → **revoke the old refresh token** → issue new access token + new refresh token. If a revoked token is presented, revoke ALL tokens (indicates theft)
4. **Logout**: Mark refresh token as revoked in `refresh_tokens` table + clear cookie
5. **Revoke all sessions**: Button in Settings that invalidates ALL refresh tokens (for suspected compromise)
6. **Server-side inactivity timeout**: Every authenticated request updates `last_activity` in settings. Token refresh is denied if last_activity > 30 min ago
7. **Rate limiting** (via `slowapi` middleware, login limits persisted in `login_attempts` table so they survive restarts):
   - Login: 5 attempts / 15 min per IP
   - Token refresh: 10 / min
   - Settings mutations: 5 / min
   - General API: 120 / min

### Credential Encryption (Key Never on Disk)
1. On first boot, user sets admin password
2. A random salt is generated and stored in `settings.fernet_salt`
3. The Fernet encryption key is **derived from the admin password** using Argon2id + the salt — the key exists only in memory, never written to disk
4. **Argon2id parameters** (locked permanently — changing them invalidates all encrypted data):
   - `time_cost=3`, `memory_cost=65536` (64 MB), `parallelism=4`, `hash_len=32`
   - Fernet key: `base64.urlsafe_b64encode(argon2_raw_hash[:32])`
5. On first login after container restart, the password is used to derive the key, which is cached in a module-level variable for the process lifetime
6. Tradier API token encrypted with Fernet before SQLite storage
7. Settings API never returns decrypted credentials — only `••••••last4`
8. **Password change is atomic**: wrapped in `BEGIN IMMEDIATE` transaction — decrypt all values with old key, derive new key, re-encrypt all values, update password hash, increment `key_version`, commit. If process crashes mid-transaction, SQLite rolls back and old password remains valid
9. **Degraded-mode token**: A second copy of the Tradier token is encrypted with the `jwt_secret` (stored in DB, not derived from password). This allows the safety monitor to operate even when the system is "locked" after a restart (see Degraded Mode below)

**CRITICAL**: Backend MUST run as `--workers 1`. Multiple uvicorn workers would cause inconsistent locked/unlocked state since the Fernet key cache is per-process.

### Degraded Mode (Safety Monitor Without Login)
After a container restart, the system is "locked" — the Fernet key (derived from admin password) is not available. However, open positions still need monitoring. The safety monitor uses the `degraded_token` (encrypted with `jwt_secret`, which IS in the DB) to:
- Check order structure and replace missing protection orders
- Detect fills and process state transitions
- Apply stepped stop ratcheting

The degraded token is updated whenever the primary Fernet-encrypted token changes. The system logs a prominent warning every 5 minutes while in degraded mode: "System locked — log in to unlock full functionality."

Jobs that remain **disabled** in degraded mode: `execute_trades` (no new entries), `generate_plans` (no new signals). Only monitoring jobs run.

### Password Recovery
**Intentional design**: There is no automated password recovery. If the admin password is forgotten:
1. Stop the container
2. Delete `trading.db`
3. Restart — run first-boot setup again
4. Re-enter Tradier credentials
5. Trade history is lost

To mitigate: On first boot, generate a **recovery key** (32-byte hex), display it ONCE in container logs with instructions to save it offline. Store its bcrypt hash in the DB. The recovery key can reset the admin password via a `/api/auth/recover` endpoint (which triggers re-encryption of all secrets). This endpoint is rate-limited to 3 attempts / hour.

### Tradier Endpoint (SSRF Protected)
The Tradier API endpoint is **NOT a free-text URL**. It is stored as an enum key:
- `"sandbox"` → hardcoded to `https://sandbox.tradier.com/v1`
- `"production"` → hardcoded to `https://api.tradier.com/v1`

The mapping is in code. The Settings page shows a dropdown with two options. No user-supplied URL is ever accepted or stored.

### Kill Switch (Re-Authentication Required)
- Kill switch disable (stop all trading) is instant — no re-auth needed
- Kill switch enable (resume trading) requires full re-auth (password + fruit challenge)
- All kill switch changes logged to `audit_log` with timestamp and IP

### Network Security
- Caddy handles HTTPS (`tls internal` for LAN, Let's Encrypt for public)
- Security headers set in Caddyfile:
  ```
  Strict-Transport-Security: max-age=31536000; includeSubDomains
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'
  Referrer-Policy: strict-origin-when-cross-origin
  -Server
  ```
- CORS: explicit origin list only (the Caddy hostname) — **never `*`**
- Backend/frontend ports NOT exposed to host — only Caddy's 443

### Logging (Credential Redaction)
- **NEVER log**: Authorization header contents, API tokens (even partially), admin password, JWT tokens, Fernet keys
- **Tradier client**: custom `__repr__` on the requests Session that redacts the Authorization header. Sanitize `e.response.text` on errors by stripping any substring matching the stored token before logging
- Use a logging filter that strips `Bearer .*` patterns AND known token substrings from all log output including stack traces
- Log files stored in tmpfs inside container (not persisted to volume alongside DB)
- `RotatingFileHandler`: 5 MB max, 3 backup files
- Error responses to unauthenticated users: generic messages only ("Authentication required", "Invalid credentials"), never internal state

### Container Security
- All containers: non-root user, `read_only: true`, `tmpfs: [/tmp]`
- Core dumps disabled: `ulimit -c 0`
- SQLite volume mounted ONLY to backend container — not shared with frontend or caddy
- Docker images pinned to SHA-256 digests (not just version tags)
- Build process includes `pip-audit` and `npm audit` checks
- Use `PyJWT` instead of `python-jose` (python-jose has known CVEs)
- nginx configured to return 404 for `.map` file requests

### Input Validation
- Symbol names: server-side validation `^[A-Z]{1,5}$` — reject anything else
- Tradier API token: validate format `^[A-Za-z0-9]{20,50}$` before storing
- All SQL queries use parameterized queries (`?` placeholders) exclusively — **never** f-strings or `.format()` for SQL

---

## DataStore Architecture (In-Memory Market Data)

**CRITICAL DESIGN DECISION**: The current production system stores all market data in PostgreSQL (`ohlcv` and `indicators` tables) and every filter/scorer queries the database via SQL. The standalone system replaces this with an **in-memory DataStore** that holds enriched DataFrames.

```python
class DataStore:
    """Singleton in-memory store for enriched market data.

    Populated daily by the data pipeline. Read by scoring, filters,
    stepped stops, and regime detector. Thread-safe via read-write lock.
    """
    _enriched: dict[str, pd.DataFrame]  # symbol -> DataFrame with all indicators
    _vix: pd.DataFrame                   # VIX daily history
    _spy: pd.DataFrame                   # SPY daily history
    _qqq: pd.DataFrame                   # QQQ daily history

    def get_latest_indicators(self, symbol: str) -> dict:
        """Returns most recent row of enriched data for a symbol."""

    def get_current_atr(self, symbol: str) -> float:
        """Returns current ATR_14 value. Used by stepped stops."""

    def get_history(self, symbol: str, days: int) -> pd.DataFrame:
        """Returns last N days of enriched data. Used by filters."""

    def get_vix_level(self) -> float:
        """Returns current VIX close."""

    def get_spy_sma100(self) -> bool:
        """Returns whether SPY is above its 100-day SMA."""
```

**Why not SQLite for market data?** WAL mode handles concurrent reads fine, but writing 100+ stocks × 250 rows of indicators daily to SQLite is unnecessary I/O. The data is ephemeral (recalculated fresh each day from Tradier API) and only needs to exist in memory during market hours. The DataStore pattern is simpler and faster.

---

## Indicator Calculations (Pure Pandas/NumPy/SciPy)

The V4 scoring engine requires these indicators, ALL calculated from raw OHLCV bars without any database dependency:

| Indicator | Library | Notes |
|-----------|---------|-------|
| SMA(20, 50, 100, 200) | pandas `.rolling().mean()` | Simple moving average |
| EMA(5, 13, 21) | pandas `.ewm().mean()` | Exponential moving average |
| RSI(14) | pandas (manual calculation) | Wilder's RSI, not simple RSI |
| Stochastic(14, 3) | pandas | %K (raw, unsmoothed) used by V4 scorer. %D (3-period SMA of %K) calculated but NOT used in scoring |
| ADX(14) | pandas (manual, Wilder's smoothing) | Directional movement index |
| ATR(14) | pandas (Wilder's smoothing) | True Range → smoothed average |
| Bollinger Bands(20, 2) | pandas | BB_UPPER, BB_LOWER, BB_PCT |
| LINEAR_REG_SLOPE | `scipy.stats.linregress` | 20-day linear regression slope of close prices |
| VOL_SURGE | pandas | Current volume / 20-day avg volume |

**CRITICAL**: V4 scoring uses Stochastic %K (raw), NOT the smoothed %D. Using %D would produce different scores.

**Validation requirement**: Before going live, run the enrichment pipeline on Tradier data and compare indicator values to the current Alpaca-derived enriched data for the same dates. All indicators must match within 0.1% tolerance (accounting for minor OHLCV differences between data providers).

---

## V4 Scoring — Exact Constants (Must Be Copied Verbatim)

The V4 scoring engine uses Bayesian-optimized constants. These must be copied exactly from `backend/utils/algorithmic_scorer.py`:

```
BASE_SCORE         = 1.908
WEIGHT_SLOPE       = 40.905
WEIGHT_ADX         = 38.900
WEIGHT_STOCH       = 25.763
WEIGHT_RSI         = 22.232
OVERBOUGHT_STOCH   = 92.351
OVERBOUGHT_RSI     = 79.824
EXTENDED_PENALTY   = -0.953
```

The `rolling_percentile()` function uses an expanding window with `min_periods=20` and a specific rank comparison. Port this function exactly — do NOT substitute with pandas `.rank()` or `.quantile()` as tie-breaking behavior may differ.

Even 1 point of V4 score difference near a sector threshold (e.g., 67.5 vs 68.5 for Semiconductors) changes buy/skip decisions.

---

## Filter Cascade (ORDER MATTERS)

Filters are applied in this exact order, matching the backtest engine (`create_filter_chain()`):

| Order | Filter | Parameters | Backtest Block Rate |
|-------|--------|------------|---------------------|
| 1 | V4 Score | Sector-specific thresholds (68-82) | ~15% |
| 2 | EMA Crossover | EMA 5×13, mode=`close_only` | ~77.5% (PRIMARY) |
| 3 | Price Movement | 0.6× ATR overnight change limit | ~3% |
| 4 | Slippage | Max 5.0% gap between signal price and entry price | ~1.4% |
| 5 | Regime | (SPY > 100-day SMA) OR (VIX < 19); block ALL if VIX ≥ 32 | ~5% |
| 6 | Sector | Block TIER_5 stocks | ~0% |
| 7 | Cash | Sufficient capital available | varies |

**CRITICAL**: The slippage filter (5% max gap) was missing from the original plan. It blocked 43 entries over 6 years in the backtest and is always enabled.

**CRITICAL**: The regime filter has two-part logic: (1) If VIX ≥ 32, ALWAYS block. (2) Otherwise, allow if (Index > SMA100) OR (VIX < 19). Both conditions must be implemented.

**CRITICAL**: The EMA crossover uses `close_only` confirmation mode. Other modes (e.g., `high_low`) produce different results.

**Signal Priority**: After filtering, signals are sorted by V4 score **descending** before capital allocation. Highest-quality signals get capital first on cash-constrained days.

---

## Scheduling (APScheduler)

All times ET, running inside the backend container.

| Time (ET) | Job | Description |
|-----------|-----|-------------|
| 12:30 PM | `fetch_daily_data` | Pull daily bars from Tradier for all watchlist symbols + VIX/SPY/QQQ, calculate all indicators, populate DataStore |
| 12:45 PM | `generate_plans` | V4 scoring → filter cascade (7 filters in order) → sort by V4 desc → generate signals |
| 1:00 PM | `execute_trades` | Process time stops first, then place orders for new signals (sorted by V4 score desc) |
| Every 2 min (market hours) | `monitor_cycle` | Combined: fill detection + state transitions + order structure enforcement + stepped stop ratcheting |
| 4:05 PM | `end_of_day` | Snapshot portfolio, fetch SPY/QQQ close, log daily summary |

**Design decisions**:
- Fill monitor + safety monitor + stepped stops **combined into one job** (`monitor_cycle` every 2 min) to avoid SQLite write collisions between three separate 1-min jobs. All writes within a single transaction to minimize lock hold time
- `AsyncIOScheduler` with `CronTrigger`, timezone `America/New_York`
- All jobs: `coalesce=True`, `max_instances=1`, `misfire_grace_time=300` (5 min)
- `execute_trades` and `generate_plans` require full Fernet key (skip if locked)
- `monitor_cycle` uses degraded-mode token if locked (safety monitoring continues)

### Startup Reconciliation

On container start, BEFORE the scheduler begins, run `reconciliation.py`:
1. Is there fresh data in the DataStore for today? If not, fetch it now (uses degraded token if locked)
2. Are there plans for today? If not and it's after 12:45 PM, generate them now (requires full key — skip if locked)
3. Are there open positions? Verify all have protection orders on Tradier
4. Are there ghost positions? (DB says open, Tradier has no position) → close in DB
5. Are there orphan positions? (Tradier has position, DB doesn't know about it) → log warning
6. Is the kill switch on or off? Log the current state
7. Is the system locked? Log a prominent warning with instructions to log in

---

## Position Lifecycle & State Machine

### States
```
ENTRY_PENDING → ENTRY_FILLED → BRACKET_PLACED → T1_FILLED → T2_FILLED → T3_FILLED → CLOSED
                                    ↓                ↓            ↓
                                  STOPPED          STOPPED      STOPPED
```

### Pyramid Handling
When the same symbol gets a new buy signal while already holding a position:
1. **Check V4 score ≥ 75** (`v4_min_pyramid_score`) — regardless of sector threshold
2. **Check pyramid_count < 2** (`max_pyramids_per_position`) — max 2 adds
3. **Cancel all existing exit orders** for the symbol (cancel-all pattern)
4. **Add new shares** (pyramid sizing: 8% of available cash, max 36% total per symbol)
5. **Recalculate blended entry price** (weighted average of all tranches)
6. **RESET all stops and targets** to new levels based on blended entry + current ATR
7. **Reset t1_hit/t2_hit/t3_hit flags** to False
8. **RESET `base_stop`** to new stop level (stepped stops ratchet from this new floor)
9. **Increment pyramid_count**
10. **Rebuild full OCO + stop order structure** with new prices and total shares

### At-Max-Position Trailing (DISTINCT from Pyramid Reset)
When a stock is already at max allocation (36%) and gets a new V4 signal:
- Do NOT add shares, do NOT reset hit flags
- DO ratchet stops/targets **UP only** (never lower)
- Update `base_stop`, `base_t1`, `base_t2`, `base_t3` and `entry_atr` if new values are higher
- This trailing adjustment keeps the best-performing stocks' stops moving up even at max allocation

### Share Distribution for T1/T2/T3
The 70/20/10 split applies to the total shares with specific rounding rules:
- **1-3 shares**: All to T1, no partial exits (T2=0, T3=0)
- **4-9 shares**: T1 gets majority, T2 gets 1-2, T3 gets 0-1
- **T3 must be ≤ T2** — swap if rounding causes inversion
- **Minimum 1 share per tier** only if total ≥ 5 (T2) or ≥ 10 (T3)
- **Rebalance residual** by adjusting T1 (T1 = total - T2 - T3)

### Edge Cases (All Must Be Handled)
| Edge Case | Description | Handling |
|-----------|-------------|----------|
| T1 instant fill | Entry + T1 fill in same cycle | Detect in monitor_cycle, skip to T2 cascade |
| Cross-trade attribution | Two positions in same symbol | Prevent via max-position-per-symbol rule |
| Ghost positions | DB says open, Tradier says none | Close in DB with exit_reason='ghost_cleanup' |
| Stop >= current price | Tradier rejects sell stop above market | Cap stop to current_price - 0.01 |
| Breakeven above current | Low ATR → breakeven offset too high | Cap breakeven at T1_price × 0.99 |
| Stuck pending_cancel | Orders that refuse to cancel | Track stuck shares, subtract from available qty |
| Sandbox instant fills | Limit orders fill immediately at market | Log warning, handle gracefully |

### Time-Based Stops
| Stop Type | Condition | Action |
|-----------|-----------|--------|
| Stagnant win | Held ≥ 20 calendar days AND ≥ 5% profit AND T1 not hit | Market sell all shares |
| Hard time stop | Held ≥ 60 calendar days | Market sell all shares |

Time stops use **calendar days** (matching `(today - entry_date).days` in production). Time stops are checked in `execute_trades` job (1:00 PM) BEFORE processing new signals.

### Breakeven Stop Logic
- After T1 fills: set stop to `entry_price + 1.0%`
- **Cap**: breakeven stop is capped at `T1_price × 0.99` (prevents stop above current price when ATR is low)
- **CRITICAL**: When breakeven is set, `base_stop` is also updated to the new breakeven level. Stepped stops ratchet from this new floor, not the original stop
- After T2 fills: lock stop to T1 price level

### Stepped Stop Logic
- Formula: `stepped_stop = base_stop + (active_days × 0.5 × current_ATR)`
- Only ratchets UP via `max(current_stop, stepped_stop)`
- Composes with breakeven/T2-lock (those can set stop higher, stepped never lowers it)
- `current_ATR` read from DataStore (dynamic, recalculated daily)
- Starts immediately (delay_days = 0)
- `base_stop` is the ratchet floor — it gets updated when breakeven is set or when pyramids reset stops

---

## What to Port From Existing Code

### From `tradier-service/`
| Source File | What to Extract | Porting Notes |
|-------------|----------------|---------------|
| `services/tradier_client.py` | API wrapper for orders, quotes, account | **Must add**: `get_market_history(symbol, interval, start, end)` — does not exist today. Add custom `__repr__` to redact auth headers |
| `services/order_manager.py` | Order placement, OCO, cascades, cancel-all | Replace SQLAlchemy ORM → raw parameterized SQL. Replace Decimal → integer cents. Port `calculate_share_distribution()` with its small-position rounding rules |
| `services/position_safety_monitor.py` | Order verification + fill detection | **Decompose** this 1,455-line monolith into `safety_monitor.py` (order enforcement) and fill detection within `monitor_cycle`. No `fill_monitor.py` exists to port — this is new design work |
| `configs/tradier_production.json` | Trading parameters reference | Extract params into `trading_params.json` |

### From `backend/`
| Source File | What to Extract | Porting Notes |
|-------------|----------------|---------------|
| `utils/algorithmic_scorer.py` | V4 scoring formula + sector thresholds | **Copy constants verbatim**. Rewrite to accept DataFrames. Port `rolling_percentile()` exactly (custom rank, min_periods=20) |
| `api/routes/trading.py` | Filter cascade (EMA, regime, price movement) | **Complete rewrite** — all filters currently build raw SQL queries. Must become pure pandas/numpy functions reading from DataStore. Add slippage filter (5% max gap) |
| `utils/regime_detector.py` | Regime detection (VIX + SMA) | Port `SectorSpecificRegimeDetector`. Move hardcoded `STOCK_TO_INDEX` mapping → `watchlist.json` `benchmark_index` field |
| `auto_generate_daily_plans.py` | Plan generation orchestration | **Rewrite** the orchestration layer. Add V4 desc sort before capital allocation |
| `paper_trading_engine.py` | `smart_round_shares()`, VIX sizing brackets | Extract just the sizing logic (~50 lines). Add sector tier multipliers |

### From `frontend/`
| Source Component | What to Extract | Porting Notes |
|------------------|----------------|---------------|
| Chart patterns (equity curve, heatmap) | Visual design reference | Rebuild with Recharts. Don't port the 27K+ line pages |
| Axios interceptor pattern | JWT auto-refresh | Clean reimplementation with refresh token rotation |

### NOT Porting
- Everything Alpaca (order manager, SSE handler, sync scripts, paper trading mirror)
- Universal backtest engine
- Dual-server failover, heartbeat, role system, recovery sync
- Data sync between servers
- NAS backup system
- Stock split handler (Tradier data is already split-adjusted)
- Discord webhook integrations — **explicitly excluded, no Discord in this system**

---

## Tradier Market Data Integration

### Phase 0: Data Feasibility Validation (BEFORE BUILDING ANYTHING)

Before writing a line of production code, validate these assumptions:

1. **VIX data available?** Test: `GET /v1/markets/history?symbol=VIX&interval=daily&start=2025-01-01&end=2025-02-25`
   - If VIX doesn't work: try `$VIX.X`, `^VIX`, `VIXY`. Fallback: CBOE or Yahoo Finance for VIX only
2. **Sufficient history depth?** Need 250+ trading days for SMA_200. Test: request 2 years of daily bars
3. **Rate limits?** How many calls/minute? With 100 stocks + VIX + SPY + QQQ = 103 history requests daily + batch quote requests per monitor cycle
4. **Data quality?** Compare Tradier daily bars vs Alpaca for 10 symbols over 1 year — verify OHLCV match within 0.5%
5. **Indicator parity?** Calculate V4 score from Tradier data vs production V4 score for same date — must match within 1 point

### API Endpoints

**Daily Bars**:
```
GET /v1/markets/history?symbol=AAPL&interval=daily&start=2024-01-01&end=2025-02-25
```
Returns: date, open, high, low, close, volume

**Batch Quotes (Realtime)**:
```
GET /v1/markets/quotes?symbols=AAPL,NVDA,MSFT,...
```
Returns: last price, bid, ask, volume, change. Supports comma-separated list (batch).

**VIX / SPY / QQQ**:
Same history endpoint. Verify exact symbol strings for indices.

### Data Pipeline (Daily at 12:30 PM ET)
1. Fetch 250 days of daily bars for each watchlist symbol (~100 stocks)
2. Fetch VIX, SPY, QQQ history (same endpoint)
3. Calculate ALL indicators using pure pandas/numpy/scipy (see Indicators section)
4. Calculate V4 score for each symbol
5. Populate DataStore singleton
6. Update `regime_state` table (VIX level, SPY > SMA100)

**Cold start**: On first boot or after extended downtime, fetch full history and rebuild. Subsequent days only need to append the latest bar and recalculate (but full recalc is cheap enough to just do it every time).

### Data Provider Disclaimer
The validated backtest (75.62% CAGR) used Alpaca split-adjusted data. Tradier data may produce slightly different OHLCV values. The standalone system implements identical LOGIC but with a different data source. Backtest numbers are a reference point, not a guarantee. Indicator and V4 score parity is validated in Phase 0, but small differences are inherent to changing data providers.

---

## Frontend Pages

### Login Page
- **Password-only field** (no username — hardcoded to "admin", single-user system)
- First-boot: shows setup form with "Setup Token" + "Choose Password" + "Confirm Password" fields
- Rate limit feedback (X attempts remaining before lockout)
- Lockout display (locked for Y minutes)
- **No `dangerouslySetInnerHTML` anywhere in the frontend**

### Dashboard Page
- **Top bar**: Account value, daily P&L ($ and %), cash available
- **Kill switch**: Prominent toggle — disable is instant, enable requires password + fruit challenge
- **Equity curve**: Line chart — account value vs SPY vs QQQ (normalized to %, from first snapshot). Use Recharts.
- **Open positions table**: Symbol, shares, entry price, current price, unrealized P&L, stop level, days held, state (T1/T2/T3), stepped stop level
- **Today's signals**: What was generated today, what was executed, what was skipped (and why)

### Trade History Page
- **Closed trades table**: Symbol, entry/exit dates, entry/exit prices, P&L ($, %), hold days, exit reason
- **Summary stats**: Win rate (position-based, not exit-based), avg win, avg loss, profit factor, total P&L
- **Monthly returns heatmap**: Color-coded grid by month/year
- **Filters**: Date range, symbol, win/loss only

### Settings Page
- **Tradier credentials**: API token (masked input, show last 4 only, never readable), environment dropdown (Sandbox / Production — NOT a URL field)
- **Kill switch**: Toggle with password re-entry + fruit confirmation
- **Connection test**: Button to verify Tradier API connectivity
- **Account info**: Account number, buying power, account type (read from Tradier API)
- **Revoke all sessions**: Button to invalidate all refresh tokens (for suspected compromise)
- **System status**: Scheduler status, last run times for each job, next scheduled runs, DataStore freshness, system "locked/unlocked" state
- **System version**: Current version (from `VERSION` file), "Check for Updates" button (read-only — compares local vs remote git refs). Shows "Up to date" or "Update available (v1.3.0 → v1.4.0) — run ./update.sh via SSH". Never auto-updates from the UI

---

## Build Phases

### Phase 0 — Data Feasibility Validation
- Write a standalone Python script (not in the container yet) that:
  - Calls Tradier market data API for VIX, SPY, 10 test stocks
  - Verifies 250+ days of history available
  - Calculates all indicators from raw bars
  - Compares V4 scores against production database values
  - Documents rate limits encountered
- **Deliverable**: GO/NO-GO decision on Tradier as data source. If NO-GO, evaluate alternatives (Alpha Vantage, Polygon, Yahoo Finance)

### Phase 1 — Container Scaffold + Auth + Security
- `setup.sh` — Docker prerequisite check, auto port detection, docker compose, URL/token printing
- `update.sh` — git pull, docker compose build, restart, health check, version display
- `VERSION` file — initial version `0.1.0`
- `.gitignore` — exclude .env, data/, *.db, __pycache__, node_modules, frontend/build
- `.env.example` — template with port and optional password vars
- Initialize new git repo
- Create docker-compose.yml (SHA-pinned images, non-root, read-only, configurable ports via .env)
- Caddyfile with TLS + security headers
- Backend: FastAPI skeleton (`--workers 1`), SQLite setup (WAL mode), migrations
- Fernet encryption with Argon2id key derivation from admin password (locked parameters)
- JWT auth: login, refresh (with rotation + revocation table), logout, server-side timeout
- First-boot setup with one-time token + recovery key generation
- Rate limiting middleware (`slowapi` + persistent `login_attempts` table)
- CORS strict origin
- Credential redaction in logging (custom Session `__repr__`, response text sanitization)
- Audit log table
- Degraded-mode token storage
- Frontend: React app scaffold, login page, auth context, CSRF token on refresh, `GENERATE_SOURCEMAP=false`
- nginx blocks `.map` file requests
- `/api/health` endpoint (boolean status only)
- **Deliverable**: Secure auth system, containers running, can login and see empty dashboard

### Phase 2 — Tradier Data Pipeline + DataStore
- Add `get_market_history()` to tradier_client.py
- Build enrichment pipeline: all indicators from raw bars (pandas/numpy/scipy)
- Implement DataStore singleton with thread-safe read/write
- V4 scoring engine (pure pandas, no SQL, verbatim constants, exact `rolling_percentile()`)
- Regime detector (ported from `regime_detector.py`, stock-to-index from `watchlist.json`)
- APScheduler with `fetch_daily_data` job (12:30 PM)
- Indicator validation against production values
- **Deliverable**: System fetches data, calculates indicators, V4 scores match production within tolerance

### Phase 3 — Signal Generation + Execution
- Rewrite ALL 7 filters as pure DataFrame operations (no SQL):
  1. V4 threshold filter (sector-specific from `watchlist.json`)
  2. EMA 5×13 crossover filter (mode=`close_only`)
  3. Price movement ATR filter (0.6×)
  4. Slippage filter (5.0% max gap — always on)
  5. Regime filter: (SPY > SMA100) OR (VIX < 19); block if VIX ≥ 32
  6. Sector tier filter (block TIER_5)
  7. Cash availability filter
- Plan generator (DataStore → filter cascade → sort by V4 desc → trade decisions)
- Position sizer (cash-based + VIX dynamic brackets [16,22,28,34] + sector tier multipliers [TIER_1=1.5×] + whole shares)
- Order manager (port from Tradier service, replace ORM → parameterized SQL, Decimal → integer cents)
- Execution job (time stops first, then new signals sorted by V4 desc)
- **Deliverable**: System generates signals matching production and places orders on Tradier

### Phase 4 — Position Management
- Combined monitor_cycle (every 2 min, works in degraded mode): fill detection + state transitions + order enforcement
- Stepped stop ratcheting (reads ATR from DataStore, `base_stop` updated on breakeven)
- Breakeven stops (1.0% offset, capped at T1 × 0.99, updates `base_stop`)
- T2 lock (stop to T1 level after T2 fills)
- Cascade bracket placement (T1 → T2 cascade, T2 → T3 bracket)
- Pyramid handling (V4 ≥ 75, max 2 pyramids, cancel all → add shares → reset stops/targets/hit flags/base_stop → rebuild orders)
- At-max-position trailing (UP only ratchet, no hit flag reset)
- Share distribution with small-position rounding rules
- Ghost position detection + cleanup
- Startup reconciliation (works in degraded mode)
- Edge case handling (all items from Edge Cases table)
- **Deliverable**: Full position lifecycle from entry to exit, matching production behavior

### Phase 5 — Frontend Dashboard + Charts
- Dashboard page (positions, account stats, equity curve vs SPY/QQQ)
- Kill switch with re-auth + fruit challenge
- Trade history page (closed trades, monthly heatmap, stats with position-based win rate)
- Settings page (masked credentials, environment dropdown, revoke sessions, system status, audit log viewer)
- Portfolio snapshot job (daily at 4:05 PM)
- Benchmark data (SPY/QQQ)
- **Deliverable**: Complete working frontend, ~4,000-5,000 lines

### Phase 6 — Hardening + Deployment
- Error handling: Tradier API down → skip cycle with warning, don't crash
- Graceful degradation: scheduler continues even if one job fails
- SQLite backup: `PRAGMA wal_checkpoint(TRUNCATE)` then `VACUUM INTO` during non-market hours, keep 7 daily backups on a second volume. Always backup `-wal` and `-shm` alongside main DB
- Database migration system: `schema_version` table, sequential idempotent migrations, auto-run at startup
- Container health checks in docker-compose.yml
- `pip-audit` + `npm audit` in build process
- Verify host has swap disabled (`swapoff -a`) for tmpfs security
- Test on target hardware (Pi 5 if applicable)
- End-to-end test of `setup.sh` on a clean machine (verify Docker install check, port detection, token display, URL printing)
- End-to-end test of `update.sh` (verify git pull, rebuild, restart, version bump, data preservation)
- Settings page: version display (from `VERSION` file) + "Check for Updates" read-only indicator
- Deployment README (should be short — the real instructions are: `git clone glyphTrader`, `./setup.sh`, open URL, enter token. Update: `./update.sh`)
- **Deliverable**: Production-ready system, one-command install, one-command update

---

## watchlist.json Structure

```json
{
  "stocks": [
    {
      "symbol": "AAPL",
      "sector": "Mega Tech",
      "tier": 1,
      "v4_threshold": 82,
      "benchmark_index": "QQQ",
      "tier_size_multiplier": 1.0
    },
    {
      "symbol": "NVDA",
      "sector": "Semiconductors",
      "tier": 1,
      "v4_threshold": 68,
      "benchmark_index": "QQQ",
      "tier_size_multiplier": 1.5
    },
    {
      "symbol": "NEE",
      "sector": "Utilities",
      "tier": 2,
      "v4_threshold": 68,
      "benchmark_index": "SPY",
      "tier_size_multiplier": 1.0
    }
  ],
  "benchmark_symbols": ["SPY", "QQQ"],
  "vix_symbol": "VIX"
}
```

Fields:
- `benchmark_index` — replaces hardcoded `STOCK_TO_INDEX` dict from `regime_detector.py`
- `v4_threshold` — sector-specific V4 minimum score for entry
- `tier_size_multiplier` — position sizing multiplier (TIER_1 semis = 1.5×, all others = 1.0×). Replaces backtest's `sector_multipliers` config
- `tier` — used for sector filter (TIER_5 = blocked)

---

## trading_params.json Structure

```json
{
  "position_sizing": {
    "initial_pct": 12.0,
    "pyramid_pct": 8.0,
    "max_per_stock_pct": 36.0,
    "use_margin": false
  },
  "pyramid": {
    "v4_min_pyramid_score": 75.0,
    "max_pyramids_per_position": 2
  },
  "vix_sizing_brackets": [16, 22, 28, 34],
  "vix_sizing_multipliers": [1.0, 0.8, 0.6, 0.4, 0.2],
  "atr_exits": {
    "stop_loss_mult": 3.3,
    "t1_target_mult": 0.7,
    "t2_target_mult": 1.5,
    "t3_target_mult": 3.0,
    "t1_exit_pct": 70,
    "t2_exit_pct": 20,
    "t3_exit_pct": 10
  },
  "stepped_stops": {
    "enabled": true,
    "step_size": 0.5,
    "delay_days": 0,
    "use_dynamic_atr": true
  },
  "breakeven": {
    "offset_pct": 1.0,
    "cap_at_t1_minus_pct": 1.0
  },
  "filters": {
    "ema_fast": 5,
    "ema_slow": 13,
    "ema_confirmation_mode": "close_only",
    "price_movement_atr_mult": 0.6,
    "max_slippage_pct": 5.0,
    "regime_vix_max": 32,
    "regime_vix_allow_below": 19,
    "regime_sma_period": 100,
    "blocked_tiers": [5]
  },
  "time_stops": {
    "stagnant_win_days": 20,
    "stagnant_win_min_profit_pct": 5.0,
    "hard_time_stop_days": 60
  },
  "entry_time": "13:00",
  "entry_delay_days": 1
}
```

---

## Open Questions (Resolve Before Building)

1. **Tradier VIX data** — Phase 0 will verify. Fallback: CBOE or Yahoo Finance API for VIX only
2. **Tradier rate limits** — Phase 0 will measure. If tight, batch quote calls (comma-separated symbols in single request)
3. **Stock universe size** — Current plan: 64 stocks. Could expand. Affects data fetch time (linear)
4. **Target hardware** — Pi 5 8GB? Mini PC/NUC? Determines nothing about software design (Docker abstracts it) but affects performance expectations
5. **LAN only or internet-facing?** — Determines Caddy TLS config (`tls internal` vs Let's Encrypt)

---

## Estimated Size (Revised)

| Component | Lines of Code (est.) | Risk Level |
|-----------|---------------------|------------|
| Backend core (scoring, filters, indicators, DataStore) | ~2,500-3,000 | HIGH — full SQL-to-pandas rewrite, verbatim constant porting |
| Tradier client + order manager | ~2,500 | MEDIUM — new market data methods, ORM-to-SQL, share rounding |
| Safety monitor + stepped stops + reconciliation | ~1,200-1,500 | HIGH — monolith decomposition, degraded mode |
| API routes + auth + middleware + audit | ~1,000 | MEDIUM — refresh rotation, degraded token, audit log |
| Database + models + crypto | ~600 | MEDIUM — integer cents, migrations, atomic password change |
| Scheduler + startup reconciliation | ~400 | MEDIUM — missed job handling, degraded mode |
| Frontend (3 pages + components) | ~4,000-5,000 | MEDIUM — charts, auth flow, no source maps |
| Config + Docker + Caddy + setup.sh + update.sh | ~400 | LOW |
| **Total** | **~12,600-14,400** | |

Compare to current system: 30,000+ lines across all services. The standalone system is ~40-45% of the current codebase but is functionally complete for its scope.
