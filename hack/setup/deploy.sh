#!/usr/bin/env bash

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_DIR=$(realpath $(pwd)/)

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

export LLMDBENCH_STEPS_DIR="$LLMDBENCH_DIR/steps"
LLMDBENCH_DRY_RUN=false
LLMDBENCH_STEP_LIST=$LLMDBENCH_STEPS_DIR"/*.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) LLMDBENCH_DRY_RUN=true ;;
    --step) LLMDBENCH_STEP_LIST="$2"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

run_step() {
  local script_name=$1
  local script_path=$(ls ${LLMDBENCH_STEPS_DIR}/${script_name}*)
  if [ -f $script_path ]; then
    local step_id=$(basename "$script_path")
    echo -e "\n=== Running step: $step_id ==="
    if [[ "$LLMDBENCH_DRY_RUN" == "true" ]]; then
      echo "[DRY RUN] Would execute: $script_path"
    else
      source $script_path
    fi
  fi
}

for step in $LLMDBENCH_STEP_LIST; do
  run_step "$step"
done

echo "✅ All steps complete."
