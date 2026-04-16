#!/usr/bin/env bash

mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
cd ${LLMDBENCH_RUN_WORKSPACE_DIR}/bench_serving/
cp -f ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/inferencemax/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
en=$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/inferencemax/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r .executable)

# Start metrics collection in background if enabled
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]]; then
  echo "Starting metrics collection..."
  /usr/local/bin/collect_metrics.sh start >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1 &
  METRICS_COLLECTOR_PID=$!
  echo "Metrics collector started with PID: $METRICS_COLLECTOR_PID"
  echo "Metrics collection logs: $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log"
fi

echo "Running warmup with 3 prompts"
python ${en} --$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/inferencemax/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^ ^g' -e 's^=none$^^g' -e 's^num-prompts=[0-9]*^num-prompts=3^') --seed $(date +%s) > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
echo "Running main benchmark"
export LLMDBENCH_HARNESS_ARGS="--$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/inferencemax/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} \
  | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' \
  | sed -e 's^=none ^ ^g' -e 's^=none$^^g') --seed $(date +%s) --save-result"
start=$(date +%s.%N)
python ${en} $LLMDBENCH_HARNESS_ARGS > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)

# Stop metrics collection
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]] && [[ -n "${METRICS_COLLECTOR_PID:-}" ]]; then
  echo "Stopping metrics collection..."
  /usr/local/bin/collect_metrics.sh stop >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1
  wait $METRICS_COLLECTOR_PID 2>/dev/null || true
  
  # Process collected metrics
  echo "Processing collected metrics..."
  /usr/local/bin/collect_metrics.sh process >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1
  
  echo "Metrics collection complete. Check metrics_collection.log for details."
fi

find ${LLMDBENCH_RUN_WORKSPACE_DIR}/bench_serving -maxdepth 1 -mindepth 1 -name '*.json' -exec mv -t "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"/ {} +

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION=$(cd /workspace/bench_serving; git rev-parse HEAD)

# Write run metadata to a file so the analyzer can read it.
# Environment variables exported here are lost when this subshell exits,
# so the file serves as the handoff mechanism to the analysis phase.
cat > "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/run_metadata.yaml" <<METADATA
harness_start: "${LLMDBENCH_HARNESS_START}"
harness_stop: "${LLMDBENCH_HARNESS_STOP}"
harness_delta: "${LLMDBENCH_HARNESS_DELTA}"
harness_args: "${LLMDBENCH_HARNESS_ARGS}"
harness_version: "${LLMDBENCH_HARNESS_VERSION}"
harness_name: "${LLMDBENCH_HARNESS_NAME:-inferencemax}"
harness_workload: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME:-}"
harness_rc: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
model: "${LLMDBENCH_DEPLOY_CURRENT_MODEL:-}"
endpoint_url: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:-}"
namespace: "${LLMDBENCH_VLLM_COMMON_NAMESPACE:-}"
METADATA
echo "Run metadata written to $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/run_metadata.yaml"

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
