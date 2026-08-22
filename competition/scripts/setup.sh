#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("R3E-AIC requires Python 3.11 or newer")
import competition
print(f"R3E-AIC {competition.__version__}: Python ready")
PY

for tool in iverilog vvp yosys; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "[ready] $tool"
  else
    echo "[missing] $tool — demos fail closed until it is installed" >&2
  fi
done

python3 "$ROOT/scripts/verify_datasets.py"
echo "Setup check complete. No provider call was made."
