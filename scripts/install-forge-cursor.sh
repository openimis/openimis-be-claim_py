#!/usr/bin/env bash
# Install Forge Cursor hooks and rules from committed templates (local-only; gitignored).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/scripts/forge-cursor-templates"
DEST="$ROOT/.cursor"

if [[ ! -d "$SRC" ]]; then
  echo "Missing templates at $SRC" >&2
  exit 1
fi

mkdir -p "$DEST/hooks" "$DEST/rules"
cp "$SRC/hooks.json" "$DEST/hooks.json"
cp "$SRC/hooks/"*.sh "$DEST/hooks/"
chmod +x "$DEST/hooks/"*.sh
cp "$SRC/rules/"*.mdc "$DEST/rules/"

echo "Installed Forge Cursor hooks and rules to $DEST"
echo "Run: configure_repo (Forge MCP, ide=cursor) if hooks are missing after clone."
