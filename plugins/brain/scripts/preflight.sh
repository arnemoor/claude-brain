#!/usr/bin/env bash
# preflight.sh - verify required tools for the brain system. Plain shell, no uv.
# Usage: preflight.sh [gh] [gitea] [cloudflare]
miss=0
need() {  # need <cmd> <install-hint>
  command -v "$1" >/dev/null 2>&1 || { echo "MISSING $1 - $2"; miss=1; }
}
need uv  "install: curl -LsSf https://astral.sh/uv/install.sh | sh   (or brew install uv). Do NOT rely on system python."
need git "install: xcode-select --install   (or brew install git)."
for src in "$@"; do
  case "$src" in
    gh)         need gh  "brew install gh, then gh auth login" ;;
    gitea)      need tea "brew install tea, then tea login add" ;;
    cloudflare) echo "NOTE cloudflare: verify the Cloudflare MCP is connected in Claude (no CLI to check)" ;;
  esac
done
if [ "$miss" -ne 0 ]; then echo "Preflight FAILED: install the MISSING tools above, then re-run."; exit 1; fi
echo "Preflight OK"; exit 0
