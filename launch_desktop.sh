#!/usr/bin/env bash
# AgentMesh Desktop App Launcher
# High-performance native WebKitGTK Container (~40MB RAM, <100ms startup) with Chrome fallback

PORT=8420
APP_DIR="/home/pro/Downloads/agent-mesh"
SERVER_SCRIPT="$APP_DIR/server.py"
DESKTOP_SCRIPT="$APP_DIR/app_desktop.py"
LOG_FILE="/home/pro/.config/agent-mesh/server.log"

mkdir -p /home/pro/.config/agent-mesh

# Function to test port availability
check_port() {
    python3 -c "import socket; s = socket.socket(); s.settimeout(0.5); res = s.connect_ex(('127.0.0.1', $PORT)); s.close(); exit(0 if res == 0 else 1)" 2>/dev/null
}

# 1. Ensure backend server daemon is running persistently
if ! check_port; then
    echo "Starting AgentMesh Core Daemon on port $PORT..."
    nohup python3 "$SERVER_SCRIPT" >>"$LOG_FILE" 2>&1 &
    disown
    for i in {1..20}; do
        if check_port; then
            break
        fi
        sleep 0.1
    done
fi

# 2. Launch Native WebKitGTK App if display available
if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
    nohup python3 "$DESKTOP_SCRIPT" >/dev/null 2>&1 &
    disown
    echo "Launched AgentMesh Native WebKitGTK Desktop App."
else
    echo "AgentMesh Server running on http://127.0.0.1:$PORT"
fi
