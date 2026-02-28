#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "glyphTrader -- Update"
echo "===================="
echo ""

# Check we're in a git repo
if [ ! -d .git ]; then
    echo "ERROR: Not a git repository"
    exit 1
fi

# Check for local modifications
if ! git diff --quiet HEAD 2>/dev/null; then
    echo "You have local modifications. Stash or discard them first."
    exit 1
fi

# Current version
CURRENT_VER=$(cat VERSION 2>/dev/null || echo "unknown")
echo "Current version: v${CURRENT_VER}"
echo "Checking for updates..."
echo ""

# Fetch
if ! git fetch origin main 2>/dev/null; then
    echo "ERROR: Failed to fetch from origin"
    exit 1
fi

# Compare
BEHIND=$(git rev-list HEAD..origin/main --count 2>/dev/null || echo "0")

if [ "$BEHIND" = "0" ]; then
    echo "Already up to date (v${CURRENT_VER})"
    exit 0
fi

echo "${BEHIND} new commits:"
git log HEAD..origin/main --oneline
echo ""

# Pull
echo -n "Pulling updates...  "
git pull origin main --ff-only
echo "done"

# New version
NEW_VER=$(cat VERSION 2>/dev/null || echo "unknown")

# Rebuild
echo -n "Building containers...  "
docker compose build --quiet
echo "done"

# Restart
echo -n "Restarting...  "
docker compose up -d
echo "done"

# Health check
echo -n "Health check...  "
for i in $(seq 1 30); do
    if docker compose exec -T backend python -c "import httpx; r=httpx.get('http://localhost:8000/api/health'); r.raise_for_status()" 2>/dev/null; then
        echo "passed"
        break
    fi
    sleep 1
    if [ "$i" = "30" ]; then
        echo "FAILED (backend not healthy after 30s)"
    fi
done

echo ""
echo "Updated: v${CURRENT_VER} -> v${NEW_VER}"

# Get URL
source .env 2>/dev/null || true
HTTPS_PORT="${TFT_HTTPS_PORT:-443}"
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
LAN_IP="${LAN_IP:-localhost}"
echo "System ready: https://${LAN_IP}:${HTTPS_PORT}"
echo ""
