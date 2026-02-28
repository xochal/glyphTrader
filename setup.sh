#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "glyphTrader"
echo "==========="
echo ""

# --- Prerequisites ---
echo "Checking prerequisites..."

if command -v docker &>/dev/null; then
    DOCKER_VER=$(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1)
    echo "  Docker:         v${DOCKER_VER}"
else
    echo "  Docker: NOT FOUND"
    echo ""
    echo "Docker is required but not installed."
    read -rp "Install Docker now? [Y/n] " yn
    yn=${yn:-Y}
    if [[ "$yn" =~ ^[Yy] ]]; then
        echo "  -> Installing via get.docker.com..."
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER" 2>/dev/null || true
        echo "  -> Docker installed"
        echo ""
        echo "NOTE: You may need to log out and back in for group changes."
        echo "      Then re-run: ./setup.sh"
        exit 0
    else
        echo "Docker is required. Install it and re-run setup.sh"
        exit 1
    fi
fi

if docker compose version &>/dev/null; then
    COMPOSE_VER=$(docker compose version --short 2>/dev/null || echo "unknown")
    echo "  Docker Compose: v${COMPOSE_VER}"
else
    echo "  Docker Compose: NOT FOUND (required)"
    exit 1
fi

# --- Warnings ---
ARCH=$(uname -m)
echo "  Architecture:   ${ARCH}"

FREE_DISK=$(df -BG . | tail -1 | awk '{print $4}' | tr -d 'G')
if [ "${FREE_DISK}" -lt 2 ] 2>/dev/null; then
    echo "  WARNING: Low disk space (${FREE_DISK}GB free, 2GB recommended)"
fi

TOTAL_MEM=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo "0")
if [ "${TOTAL_MEM}" -lt 1024 ] 2>/dev/null; then
    echo "  WARNING: Low memory (${TOTAL_MEM}MB, 1024MB recommended)"
fi

echo ""

# --- .env Setup ---
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
fi

# Read ports from .env
source .env 2>/dev/null || true
HTTPS_PORT="${TFT_HTTPS_PORT:-443}"
HTTP_PORT="${TFT_HTTP_PORT:-80}"

# --- Admin Password (first-time only) ---
if [ ! -f data/trading.db ]; then
    echo "First-time setup — choose an admin password."
    echo "(Minimum 8 characters)"
    echo ""
    while true; do
        read -rsp "  Admin password: " ADMIN_PW
        echo ""
        if [ ${#ADMIN_PW} -lt 8 ]; then
            echo "  Password must be at least 8 characters. Try again."
            continue
        fi
        read -rsp "  Confirm password: " ADMIN_PW2
        echo ""
        if [ "$ADMIN_PW" != "$ADMIN_PW2" ]; then
            echo "  Passwords don't match. Try again."
            continue
        fi
        break
    done
    # Write to .env (consumed on first boot, then removed)
    # Remove existing line if present, then append (handles special chars safely)
    sed -i '/^ADMIN_PASSWORD=/d' .env 2>/dev/null || true
    printf 'ADMIN_PASSWORD=%s\n' "$ADMIN_PW" >> .env
    echo ""
fi

# --- Port Check ---
echo "Checking ports..."

check_port() {
    ss -tlnp 2>/dev/null | grep -q ":$1 " && return 1 || return 0
}

if check_port "$HTTPS_PORT" && check_port "$HTTP_PORT"; then
    echo "  Port ${HTTPS_PORT}: available"
    echo "  Port ${HTTP_PORT}:  available"
else
    echo "  Port ${HTTPS_PORT} or ${HTTP_PORT}: in use"
    # Try fallbacks
    for OFFSET in 8000 9000 10000; do
        TRY_HTTPS=$((OFFSET + 443))
        TRY_HTTP=$((OFFSET + 80))
        if check_port "$TRY_HTTPS" && check_port "$TRY_HTTP"; then
            HTTPS_PORT=$TRY_HTTPS
            HTTP_PORT=$TRY_HTTP
            echo "  Trying ${HTTPS_PORT}/${HTTP_PORT}... available"
            # Update .env
            sed -i "s/^TFT_HTTPS_PORT=.*/TFT_HTTPS_PORT=${HTTPS_PORT}/" .env 2>/dev/null || echo "TFT_HTTPS_PORT=${HTTPS_PORT}" >> .env
            sed -i "s/^TFT_HTTP_PORT=.*/TFT_HTTP_PORT=${HTTP_PORT}/" .env 2>/dev/null || echo "TFT_HTTP_PORT=${HTTP_PORT}" >> .env
            echo "  -> Saved to .env"
            break
        fi
    done
fi

echo ""

# --- Create data directory ---
mkdir -p data
chmod 777 data

# --- Start Containers ---
echo "Starting containers..."
docker compose up -d --build

echo ""
echo "Waiting for backend..."

# Poll health check
for i in $(seq 1 30); do
    if docker compose exec -T backend python -c "import httpx; r=httpx.get('http://localhost:8000/api/health'); r.raise_for_status()" 2>/dev/null; then
        break
    fi
    sleep 1
done

# --- Clean up ADMIN_PASSWORD from .env ---
if grep -q '^ADMIN_PASSWORD=' .env 2>/dev/null; then
    sed -i '/^ADMIN_PASSWORD=/d' .env
fi

# --- Detect first run ---
RECOVERY_KEY=$(docker compose logs backend 2>&1 | grep -oP 'RECOVERY KEY \(save this!\): \K\S+' | tail -1)
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
LAN_IP="${LAN_IP:-localhost}"

echo ""
echo "========================================="
echo "  glyphTrader is running!"
echo ""
echo "  Open: https://${LAN_IP}:${HTTPS_PORT}"
if [ -n "$RECOVERY_KEY" ]; then
    echo ""
    echo "  RECOVERY KEY (save this somewhere safe!):"
    echo "  ${RECOVERY_KEY}"
fi
echo "========================================="
echo ""
