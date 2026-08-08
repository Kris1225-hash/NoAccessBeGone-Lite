#!/usr/bin/env bash
# NoAccessBeGoneLite installer for macOS / Linux
set -euo pipefail

PLUGIN_NAME="noAccessBeGoneLite"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENCORD_DIR="${VENCORD_DIR:-$HOME/Vencord}"

if ! command -v pnpm >/dev/null 2>&1; then
    echo "[NoAccessBeGoneLite] pnpm not found. Install it first:"
    echo "    npm install -g pnpm"
    exit 1
fi

if [ ! -d "$VENCORD_DIR" ]; then
    echo "[NoAccessBeGoneLite] cloning Vencord into $VENCORD_DIR ..."
    git clone --depth 1 https://github.com/Vendicated/Vencord.git "$VENCORD_DIR"
fi

echo "[NoAccessBeGoneLite] copying plugin ..."
rm -rf "$VENCORD_DIR/src/plugins/$PLUGIN_NAME"
mkdir -p "$VENCORD_DIR/src/plugins/$PLUGIN_NAME/components"
cp "$REPO_DIR/plugin-lite/index.tsx" "$VENCORD_DIR/src/plugins/$PLUGIN_NAME/index.tsx"
cp "$REPO_DIR/plugin-lite/style.css" "$VENCORD_DIR/src/plugins/$PLUGIN_NAME/style.css"
cp "$REPO_DIR/plugin-lite/components/LockScreen.tsx" "$VENCORD_DIR/src/plugins/$PLUGIN_NAME/components/LockScreen.tsx"

echo "[NoAccessBeGoneLite] patching Vencord CSP allowlist (nab.enby.fish) ..."
if ! grep -q "nab.enby.fish" "$VENCORD_DIR/src/main/csp/index.ts"; then
    sed -i.bak 's|"icons.duckduckgo.com": ImageSrc, // DuckDuckGo Favicon API (Reverse Image Search)|&\n    "nab.enby.fish": ConnectSrc, // NoAccessBeGone name database|' "$VENCORD_DIR/src/main/csp/index.ts"
fi

cd "$VENCORD_DIR"
pnpm install --no-frozen-lockfile
pnpm build

echo
echo "[NoAccessBeGoneLite] done! Now load it:"
echo
echo "  Discord desktop: quit Discord, then: cd $VENCORD_DIR && pnpm inject"
echo "  Vesktop: Vencord settings -> Vesktop -> Open Developer Settings ->"
echo "           Vencord Location -> Change -> select $VENCORD_DIR/dist"
echo "  Then enable NoAccessBeGoneLite in Vencord settings and restart."
