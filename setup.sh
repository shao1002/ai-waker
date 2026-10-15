#!/usr/bin/env bash
# Local checkout only. No curl-to-shell, sudo, package installs or automatic logins.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--apply" ) ]]; then
  echo 'Usage: bash setup.sh [--apply] (default: preview only)' >&2
  exit 2
fi
if ! command -v python3 >/dev/null; then
  echo 'Python 3.9+ is required. Install python3 first.' >&2
  exit 1
fi
if [[ ! -f "$PROJECT_DIR/scripts/config.local.json" ]]; then
  cp -- "$PROJECT_DIR/scripts/config.example.json" "$PROJECT_DIR/scripts/config.local.json"
fi
python3 "$PROJECT_DIR/scripts/check.py"
python3 -m unittest discover -s "$PROJECT_DIR/scripts" -p 'test_*.py'
chmod +x "$PROJECT_DIR/scripts/wake.sh"
bash "$PROJECT_DIR/scripts/wake.sh" --dry-run
python3 "$PROJECT_DIR/scripts/install_cron.py" "$@"
