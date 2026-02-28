# glyphTrader

Standalone Tradier-only trading system. Runs the TFT V4/V7 strategy — 100% technical analysis, zero AI/ML.

Slim, containerized, portable. Single server, single user.

## Requirements

- **Docker** (20.10+) and **Docker Compose** (v2)
- 1 GB RAM, 2 GB disk
- Tradier brokerage account (sandbox or production)

## Quick Start

### Linux

```bash
git clone https://github.com/xochal/glyphTrader.git
cd glyphTrader
chmod +x setup.sh
./setup.sh
```

The script will prompt for an admin password, check ports, build containers, and print your access URL and recovery key.

### macOS / Windows (Docker Desktop)

The `setup.sh` and `update.sh` scripts use Linux-specific commands (`/proc/meminfo`, `ss`, GNU `grep -P`, GNU `sed -i`, `hostname -I`) and will not run on macOS or Windows. On these platforms, set up manually:

```bash
git clone https://github.com/xochal/glyphTrader.git
cd glyphTrader

# 1. Create .env
cp .env.example .env

# 2. Set your admin password (consumed on first boot, then remove the line)
echo 'ADMIN_PASSWORD=your-password-here' >> .env

# 3. (Optional) Change ports if 443/80 are in use
#    Edit .env: TFT_HTTPS_PORT=8443  TFT_HTTP_PORT=8080

# 4. Create data directory
mkdir -p data

# 5. Build and start
docker compose up -d --build

# 6. Wait for healthy, then check logs for recovery key
docker compose logs backend | grep "RECOVERY KEY"

# 7. Remove ADMIN_PASSWORD from .env
#    macOS: sed -i '' '/^ADMIN_PASSWORD=/d' .env
#    Windows: edit .env manually
```

Open `https://localhost` (or `https://localhost:8443` if you changed ports). Accept the self-signed certificate warning.

**Save your recovery key** — it is shown only once and is needed if you forget your password.

## Updating

### Linux

```bash
./update.sh
```

### macOS / Windows

```bash
git pull origin main --ff-only
docker compose build --quiet
docker compose up -d
```

Your settings and trade history (in `data/trading.db`) survive updates — the database is on a mounted volume outside the containers.

### In-App Update Notifications

For private repositories, glyphTrader needs a GitHub Personal Access Token to check for available updates. Without it, command-line updates (`git pull` / `update.sh`) still work — you just won't see the "Update Available" chip in the nav bar.

1. Create a [fine-grained PAT](https://github.com/settings/tokens?type=beta) with **read-only** access to your glyphTrader repo (Contents permission only)
2. Add it to `.env`:
   ```bash
   echo 'GITHUB_TOKEN=ghp_your_token_here' >> .env
   ```
3. Restart the backend:
   ```bash
   docker compose up -d backend
   ```

## Architecture

```
Internet/LAN
     |
   Caddy (auto HTTPS, security headers)
   /    \
React    FastAPI + APScheduler
(nginx)  (single worker, SQLite WAL)
          |
        Tradier API (execution + market data)
```

| Component | Technology |
|-----------|-----------|
| Reverse Proxy | Caddy 2.7 (auto HTTPS, HSTS, CSP) |
| Backend | Python 3.11 / FastAPI / APScheduler |
| Frontend | React (6 pages) |
| Database | SQLite (WAL mode, integer cents) |
| Broker | Tradier (execution + market data) |
| Auth | bcrypt + Argon2id + Fernet + JWT |

## Pages

| Page | Description |
|------|-------------|
| Dashboard | Portfolio overview, open positions, today's signals, regime state |
| Auto Trades | Positions opened by the automated strategy |
| Manual Trades | Orphan adoption, configurable stops/targets, hold mode |
| Trade History | Closed trades with P&L, monthly heatmap, filters |
| Trade Settings | Configurable filters, exits, position sizing, stock universe |
| Settings | Tradier credentials, password, kill switch, observe-only, system status |

## Configuration

Trading parameters are shipped as JSON defaults and can be customized per-user through the Trade Settings page. Customizations are stored in SQLite (survives container rebuilds). Reset to defaults at any time.

**Configurable**: position sizing, entry filters, exit targets, stop management, pyramid rules, time stops, VIX sizing brackets, stock universe.

**Locked (algorithm constants)**: V4 scoring weights, indicator periods.

## Paper Trading Mode

By default, glyphTrader runs in **paper trading mode** (Tradier sandbox). The "Production" environment option is locked until a valid license key is activated.

### How It Works

- **No license key** — Only sandbox/paper trading is available. The Production option in the environment dropdown is disabled.
- **With license key** — Production (live) trading is unlocked. An amber "PAPER TRADING MODE" banner disappears.
- **Version-locked** — License keys are tied to a specific version. When you update glyphTrader, the key is automatically invalidated and the system reverts to sandbox.

### Getting a Production License Key

1. Read [WAIVER.md](WAIVER.md) in its entirety
2. Paper trade for at least 30 days (90 days recommended) to understand the system's behavior
3. Contact the project maintainer to request a key for your current version
4. In the web UI, go to **Settings > Production License** and paste the `GT-...` key
5. Click **Activate License** — the Production environment option unlocks

After each software update, you must request a new key (this is intentional — code changes may affect trading behavior).

## Disclaimer

This software is provided "as-is" without warranty of any kind. It is a personal trading tool, not investment advice. The developer is not a registered investment adviser. Trading stocks involves substantial risk of loss. You are solely responsible for all trading decisions and their outcomes.

See [WAIVER.md](WAIVER.md) for the full assumption of risk and liability waiver.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
