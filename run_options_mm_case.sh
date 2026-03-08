#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

OUT_DIR="${1:-outputs}"
STEPS="${2:-360}"
SEED="${3:-7}"
PROGRESS_EVERY="${4:-60}"
SCENARIO="${5:-calm_market}"
LOG_MODE="${6:-compact}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "[options] Python was not found on PATH."
  exit 1
fi

echo "============================================================="
echo "  Options Market Making Case Study"
echo "  Black-Scholes, quote skew, toxic flow, hedging, PnL"
echo "============================================================="

if ! "$PYTHON_BIN" -c "import matplotlib; import lob_sim.cli" >/dev/null 2>&1; then
  echo "[options] Python dependencies are missing."
  echo "[options] Run: pip install -r requirements.txt"
  exit 1
fi

echo "[options] Launching case study..."
echo "[options] Scenario: ${SCENARIO}"
echo "[options] Steps: ${STEPS}"
echo "[options] Seed: ${SEED}"
echo "[options] Output folder: ${OUT_DIR}"
echo "[options] Guide: docs/options_mm_demo_guide.md"
echo "[options] Screen-share flow: terminal summary -> demo_report.md -> fills.csv -> pnl_timeseries.csv -> pnl_over_time.png"
echo

"$PYTHON_BIN" -u -m lob_sim.cli options-demo \
  --out-dir "${OUT_DIR}" \
  --steps "${STEPS}" \
  --seed "${SEED}" \
  --scenario "${SCENARIO}" \
  --verbose \
  --progress-every "${PROGRESS_EVERY}" \
  --log-mode "${LOG_MODE}"

echo
echo "[options] Run complete."
echo "[options] Screen-share order:"
echo "[options]   1. ${OUT_DIR}/demo_report.md"
echo "[options]   2. ${OUT_DIR}/fills.csv"
echo "[options]   3. ${OUT_DIR}/pnl_timeseries.csv"
echo "[options]   4. ${OUT_DIR}/checkpoints.csv"
echo "[options]   5. ${OUT_DIR}/pnl_over_time.png"
