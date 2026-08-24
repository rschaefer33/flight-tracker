#!/usr/bin/env bash
# Loads .env then runs one sweep.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a; source .env; set +a
fi
python3 tracker.py
