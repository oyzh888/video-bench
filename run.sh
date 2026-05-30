#!/usr/bin/env bash
# Convenience wrapper.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 run.py "$@"
