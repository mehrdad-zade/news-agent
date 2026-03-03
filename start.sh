echo "Starting the server and public tunnel ..."
#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# ---------------------------------------------------------------------------
# Python virtual environment & dependencies
# ---------------------------------------------------------------------------
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt -q 2>/dev/null
else
    source venv/bin/activate
    pip install -r requirements.txt -q 2>/dev/null
fi

# ---------------------------------------------------------------------------
# Pinggy uses SSH – no binary to install (ssh is built into macOS)
# Validate that ssh is available (it always is on macOS)
# ---------------------------------------------------------------------------
if ! command -v ssh &> /dev/null; then
    echo "ERROR: ssh not found. Please install OpenSSH."
    exit 1
fi

# ---------------------------------------------------------------------------
# Start FastAPI in the background, wait until it's actually accepting
# ---------------------------------------------------------------------------
UVICORN_LOG="/tmp/uvicorn_news_agent.log"
PYTHONWARNINGS=ignore PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --log-level error > "$UVICORN_LOG" 2>&1 &
UVICORN_PID=$!

for i in $(seq 1 30); do
    sleep 1
    if curl -s http://127.0.0.1:8000/ > /dev/null 2>&1; then
        break
    fi
done

# ---------------------------------------------------------------------------
# Start Pinggy tunnel via SSH
# ---------------------------------------------------------------------------
PINGGY_LOG="/tmp/pinggy_news_agent.log"
rm -f "$PINGGY_LOG"

# Kill any existing SSH tunnel to pinggy.io to avoid "token already active" error
pkill -f "pinggy.io" 2>/dev/null || true
sleep 1

if [ -n "$PINGGY_TOKEN" ]; then
    ssh -p 443 \
        -R0:127.0.0.1:8000 \
        -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o LogLevel=ERROR \
        "${PINGGY_TOKEN}@a.pinggy.io" \
        > "$PINGGY_LOG" 2>&1 &
else
    ssh -p 443 \
        -R0:127.0.0.1:8000 \
        -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o LogLevel=ERROR \
        a.pinggy.io \
        > "$PINGGY_LOG" 2>&1 &
fi
PINGGY_PID=$!

# ---------------------------------------------------------------------------
# Wait for Pinggy to print the public URL
# ---------------------------------------------------------------------------
PUBLIC_URL=""
for i in $(seq 1 20); do
    sleep 1
    PUBLIC_URL=$(grep -oE 'https://[a-zA-Z0-9_.-]+' "$PINGGY_LOG" 2>/dev/null | head -1 || true)
    if [ -n "$PUBLIC_URL" ]; then
        break
    fi
done

# Write public URL to file so the running app can read it
echo "${PUBLIC_URL}" > /tmp/news_agent_public_url

# ---------------------------------------------------------------------------
# Print the public endpoint
# ---------------------------------------------------------------------------
echo ""
echo "========================================================"
echo "  News Agent is LIVE"
echo "========================================================"
echo "  Local :  http://localhost:8000"
if [ -n "$PUBLIC_URL" ]; then
echo "  Public:  ${PUBLIC_URL}"
echo ""
echo "  Summary endpoint (share this):"
echo "  ${PUBLIC_URL}/api/summary"
echo ""
echo "  Docs:    ${PUBLIC_URL}/docs"
else
echo "  Public:  (tunnel URL not captured – check $PINGGY_LOG)"
fi
echo "========================================================"
echo ""
echo "  Press Ctrl+C to stop."
echo ""

# ---------------------------------------------------------------------------
# Keep running; kill both on exit
# ---------------------------------------------------------------------------
cleanup() {
    echo ""
    echo "[start] Shutting down..."
    kill "$UVICORN_PID" 2>/dev/null || true
    kill "$PINGGY_PID"  2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

wait "$UVICORN_PID"
