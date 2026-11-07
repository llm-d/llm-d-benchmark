#!/usr/bin/env bash
# Copyright 2026 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");

set -uo pipefail

echo "Using experiment result dir: ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
mkdir -p "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
pushd "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}" > /dev/null 2>&1

if [[ -f "${HOME:-/root}/.kube/config" && -f "/var/run/secrets/kubernetes.io/serviceaccount/token" ]]; then
  if ! kubectl --request-timeout=5s get pods -n "${LLMDBENCH_VLLM_COMMON_NAMESPACE:-default}" >/dev/null 2>&1; then
    echo "Host kubeconfig failed inside container (exec auth plugin unavailable); falling back to in-cluster ServiceAccount."
    rm -f "${HOME:-/root}/.kube/config"
    unset KUBECONFIG
  fi
fi

HARNESS_DIR="${LLMDBENCH_RUN_WORKSPACE_DIR:-/workspace}/harnesses"
if [[ ! -f "${HARNESS_DIR}/async_batch.py" ]] && [[ -f "/usr/local/bin/async_batch.py" ]]; then
  HARNESS_DIR="/usr/local/bin"
fi
COLLECT_METRICS_BIN="/usr/local/bin/collect_metrics.sh"
if [[ ! -x "${COLLECT_METRICS_BIN}" ]] && [[ -f "${HARNESS_DIR}/collect_metrics.sh" ]]; then
  COLLECT_METRICS_BIN="bash ${HARNESS_DIR}/collect_metrics.sh"
fi

WORKLOAD_FILE="${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME:-sanity_async.yaml}"
PROFILE_SRC="${LLMDBENCH_RUN_WORKSPACE_DIR:-/workspace}/profiles/async-batch/${WORKLOAD_FILE}"
if [[ -f "${PROFILE_SRC}" ]]; then
  cp "${PROFILE_SRC}" "./${WORKLOAD_FILE}"
fi
export LLMDBENCH_HARNESS_ARGS="--config $(realpath "./${WORKLOAD_FILE}" 2>/dev/null || echo "./${WORKLOAD_FILE}")"

# Default to 5s polling for async-batch so scale-from-zero cold starts and
# cooldown windows are integrated at fine temporal resolution.
export METRICS_COLLECTION_INTERVAL="${METRICS_COLLECTION_INTERVAL:-5}"

# Async Goodput requires replica_status_timeseries.json; enable collector unless explicitly disabled.
SCRAPE_ENABLED="${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-true}"
METRICS_COLLECTOR_PID=""
if [[ "${SCRAPE_ENABLED}" != "false" ]]; then
  echo "Starting metrics & replica allocation collection (interval=${METRICS_COLLECTION_INTERVAL}s)..."
  ${COLLECT_METRICS_BIN} start >> "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/metrics_collection.log" 2>&1 &
  METRICS_COLLECTOR_PID=$!
  echo "Metrics collector started with PID: ${METRICS_COLLECTOR_PID}"
fi

start=$(date +%s.%N)
python3 "${HARNESS_DIR}/async_batch.py" \
  --config "./${WORKLOAD_FILE}" \
  --results-dir "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}" \
  > >(tee -a "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/stdout.log") \
  2> >(tee -a "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/stderr.log" >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)

if [[ -n "${METRICS_COLLECTOR_PID}" ]]; then
  echo "Taking final post-window snapshot and stopping metrics collection..."
  ${COLLECT_METRICS_BIN} snapshot >> "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/metrics_collection.log" 2>&1 || true
  ${COLLECT_METRICS_BIN} stop >> "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/metrics_collection.log" 2>&1 || true
  wait "${METRICS_COLLECTOR_PID}" 2>/dev/null || true

  echo "Processing collected metrics and computing Allocated GPU-Hours..."
  ${COLLECT_METRICS_BIN} process >> "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/metrics_collection.log" 2>&1 || true
fi

if [[ -f "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/results.json" ]]; then
  python3 "${HARNESS_DIR}/async_batch.py" \
    --finalize-only \
    --results-dir "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}" \
    >> "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/stdout.log" 2>> "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/stderr.log" || true
fi

export LLMDBENCH_HARNESS_START=$(date -u -d "@${start}" --iso-8601=seconds 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")
export LLMDBENCH_HARNESS_STOP=$(date -u -d "@${stop}" --iso-8601=seconds 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")
export LLMDBENCH_HARNESS_DELTA="PT$(python3 -c "print(round(${stop} - ${start}, 3))")S"
export LLMDBENCH_HARNESS_VERSION="1.0.0"

_yaml_escape() {
  local text="${1:-}" out="" index character
  text="${text//\\/\\\\}"
  text="${text//\"/\\\"}"
  text="${text//$'\t'/\\t}"
  text="${text//$'\n'/\\n}"
  for (( index=0; index<${#text}; index++ )); do
    character="${text:index:1}"
    if [[ "$character" == [[:cntrl:]] ]]; then
      printf -v character '\\x%02x' "'$character"
    fi
    out+="$character"
  done
  printf '%s' "$out"
}
_description_text="$(_yaml_escape "${LLMDBENCH_DESCRIPTION_TEXT:-}")"
_description_keywords="$(_yaml_escape "${LLMDBENCH_DESCRIPTION_KEYWORDS:-}")"
cat > "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/run_metadata.yaml" <<METADATA
harness_start: "${LLMDBENCH_HARNESS_START}"
harness_stop: "${LLMDBENCH_HARNESS_STOP}"
harness_delta: "${LLMDBENCH_HARNESS_DELTA}"
harness_args: "${LLMDBENCH_HARNESS_ARGS}"
harness_version: "${LLMDBENCH_HARNESS_VERSION}"
harness_name: "${LLMDBENCH_HARNESS_NAME:-async-batch}"
harness_workload: "${WORKLOAD_FILE}"
harness_rc: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
experiment_id: "${LLMDBENCH_RUN_EXPERIMENT_ID:-}"
model: "${LLMDBENCH_DEPLOY_CURRENT_MODEL:-}"
endpoint_url: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:-}"
namespace: "${LLMDBENCH_VLLM_COMMON_NAMESPACE:-}"
description_text: "${_description_text}"
description_keywords: "${_description_keywords}"
METADATA

popd > /dev/null 2>&1 || true

if [[ ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC} -ne 0 ]]; then
  echo "async-batch harness returned error ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}" >&2
  exit "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
fi
echo "async-batch harness completed successfully."
exit 0
