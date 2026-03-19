#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
exec bash run_options_mm_case.sh "$@"
