#!/usr/bin/env bash
# nab-peer setup — macOS / Linux
# Auto-registers an account from this machine (residential IP), or falls back
# to a manually donated token, then registers autostart.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="$HOME/.nab/peer.env"
SHARE_KEY="a436975c7eb45eadac09659e4dce92f9f2207c8be40bfadc"

echo "== nab peer setup =="
echo "This machine will run a scanning peer for the nab name database."
echo
echo "trying to auto-register a fresh account from this IP..."
TOKEN="$(/usr/bin/python3 "$DIR/nab_peer.py" --register 2>/dev/null || true)"

if [ -z "$TOKEN" ] || ! grep -q "TOKEN=" "$HOME/.nab/peer.env" 2>/dev/null; then
    echo
    echo "auto-register failed (or the account needs a phone)."
    echo "Paste a token from a dedicated alt account instead:"
    read -r -p "Discord token: " TOKEN
    if [ -z "$TOKEN" ]; then
        echo "no token given, aborting"
        exit 1
    fi
fi

if [ -f "$HOME/.nab/peer.env" ]; then
    echo "config already exists (from auto-register) — keeping it"
else
    mkdir -p "$HOME/.nab"
    cat > "$CONF" << EOF
# nab peer config — created $(date -u +%Y-%m-%dT%H:%M:%SZ)
TOKEN=$TOKEN
SHARE_KEY=$SHARE_KEY
SCAN=1
DAILY_CAP=100
JOIN_INTERVAL=60
EOF
    chmod 600 "$CONF"
    echo "config written to $CONF"
fi

if [ "$(uname)" = "Darwin" ]; then
    PLIST="$HOME/Library/LaunchAgents/xyz.nab.peer.plist"
    cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>xyz.nab.peer</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>$DIR/nab_peer.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "autostart registered (launchd). peer is running."
else
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/nab-peer.service" << EOF
[Unit]
Description=nab scanning peer
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $DIR/nab_peer.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now nab-peer
    echo "autostart registered (systemd user unit). peer is running."
fi

echo
echo
echo "watch it live:  http://localhost:8092"
echo "done. the peer is now scanning servers in the background."
echo "stop it anytime with:"
echo "  mac: launchctl unload ~/Library/LaunchAgents/xyz.nab.peer.plist"
echo "  linux: systemctl --user stop nab-peer"
