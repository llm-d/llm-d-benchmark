#!/usr/bin/env bash
# Copyright 2026 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");

set -euo pipefail

RESULTS_DIR="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR:-.}"
RESULTS_FILE="${RESULTS_DIR}/results.json"

if [[ ! -f "${RESULTS_FILE}" ]]; then
  echo "async-batch results not found: ${RESULTS_FILE}" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HARNESS_DIR="${SCRIPT_DIR}/../../../workload/harnesses"

HARNESS_DIR="${LLMDBENCH_RUN_WORKSPACE_DIR:-/workspace}/harnesses"
if [[ ! -f "${HARNESS_DIR}/async_batch.py" ]]; then
  if [[ -f "${REPO_HARNESS_DIR}/async_batch.py" ]]; then
    HARNESS_DIR="${REPO_HARNESS_DIR}"
  elif [[ -f "/usr/local/bin/async_batch.py" ]]; then
    HARNESS_DIR="/usr/local/bin"
  fi
fi

# Re-run metrics processing & goodput finalization if metrics exist
METRICS_DIR="${RESULTS_DIR}/metrics"
if [[ -d "${METRICS_DIR}/processed" ]] && [[ -f "${HARNESS_DIR}/process_metrics.py" ]]; then
  METRICS_DIR="${METRICS_DIR}" LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR="${RESULTS_DIR}" \
    python3 "${HARNESS_DIR}/process_metrics.py" >> "${RESULTS_DIR}/stderr.log" 2>&1 || true
fi

if [[ -f "${HARNESS_DIR}/async_batch.py" ]]; then
  python3 "${HARNESS_DIR}/async_batch.py" \
    --finalize-only \
    --results-dir "${RESULTS_DIR}" >> "${RESULTS_DIR}/stdout.log" 2>> "${RESULTS_DIR}/stderr.log" || true
fi

# Convert to Benchmark Report v0.2 if the installed benchmark-report CLI supports async-batch;
# otherwise leave conversion to the workstation driver pass (which bundles native_to_br0_2.py).
if command -v benchmark-report >/dev/null 2>&1; then
  if benchmark-report "${RESULTS_FILE}" -b 0.2 -w async-batch \
    "${RESULTS_DIR}/benchmark_report_v0.2,_results.json.yaml" \
    2>> "${RESULTS_DIR}/stderr.log"; then
    echo "Converted async-batch results to Benchmark Report v0.2."
  else
    rm -f "${RESULTS_DIR}/benchmark_report_v0.2,_results.json.yaml"
    echo "In-pod benchmark-report CLI does not yet register async-batch; driver analysis will convert."
  fi
fi

if [[ -f "/usr/local/bin/visualize_metrics.py" ]] && [[ -f "${METRICS_DIR}/processed/metrics_summary.json" ]]; then
  mkdir -p "${RESULTS_DIR}/analysis"
  python3 /usr/local/bin/visualize_metrics.py "${METRICS_DIR}" 2>&1 | tee -a "${RESULTS_DIR}/stderr.log" || true
  python3 /usr/local/bin/visualize_metrics.py "${METRICS_DIR}" \
    -o "${RESULTS_DIR}/analysis/graphs" 2>&1 | tee -a "${RESULTS_DIR}/stderr.log" || true
fi

echo "async-batch analysis complete: ${RESULTS_DIR}/async_goodput_summary.json"
