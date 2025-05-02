#!/usr/bin/env bash

set -euo pipefail

STEPS_DIR="$(dirname "$0")/steps"
DRY_RUN=false
STEP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --step) STEP="$2"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

run_step() {
  local script_name=$1
  local step_id=$(basename "$script_name")
  if [[ -z "$STEP" || "$STEP" == "$step_id" ]]; then
    echo -e "\n=== Running step: $step_id ==="
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "[DRY RUN] Would execute: $script_name"
    else
      bash "$script_name"
    fi
  fi
}

for step in "$STEPS_DIR"/*.sh; do
  run_step "$step"
done

echo "✅ All steps complete."
