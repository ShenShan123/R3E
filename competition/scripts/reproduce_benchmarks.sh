#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
python3 "$ROOT/scripts/verify_datasets.py"
python3 -m competition.cli benchmark
echo "Benchmark inputs verified. No unseeded model benchmark was launched."
