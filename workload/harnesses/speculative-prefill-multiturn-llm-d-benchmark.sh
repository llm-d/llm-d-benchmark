#!/usr/bin/env bash

set -euo pipefail

echo Using experiment result dir: "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"

profile_path="${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/speculative-prefill-multiturn/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}"
if [[ ! -f "$profile_path" ]]; then
  echo "ERROR: workload profile not found: $profile_path" >&2
  exit 1
fi

start=$(date +%s.%N)
python3 "${LLMDBENCH_RUN_WORKSPACE_DIR}/harnesses/speculative-prefill-multiturn.py" \
  --profile "$profile_path" \
  --output "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json" \
  > >(tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log") \
  2> >(tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log" >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION="speculative-prefill-multiturn"

cat > "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/run_metadata.yaml" <<METADATA
harness_start: "${LLMDBENCH_HARNESS_START}"
harness_stop: "${LLMDBENCH_HARNESS_STOP}"
harness_delta: "${LLMDBENCH_HARNESS_DELTA}"
harness_args: "--profile ${profile_path} --output ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/results.json"
harness_version: "${LLMDBENCH_HARNESS_VERSION}"
harness_name: "${LLMDBENCH_HARNESS_NAME:-speculative-prefill-multiturn}"
harness_workload: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME:-}"
harness_rc: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
experiment_id: "${LLMDBENCH_RUN_EXPERIMENT_ID:-}"
model: "${LLMDBENCH_DEPLOY_CURRENT_MODEL:-}"
endpoint_url: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:-}"
namespace: "${LLMDBENCH_VLLM_COMMON_NAMESPACE:-}"
METADATA

exit "$LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"