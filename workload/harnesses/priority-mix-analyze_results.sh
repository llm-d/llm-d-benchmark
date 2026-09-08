#!/usr/bin/env bash

set -euo pipefail

results_file="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/results.json"
if [[ ! -f "${results_file}" ]]; then
  echo "priority-mix results not found: ${results_file}" >&2
  exit 1
fi

echo "priority-mix results available at ${results_file}"