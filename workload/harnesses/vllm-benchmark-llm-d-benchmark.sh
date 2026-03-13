#!/usr/bin/env bash

mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
cd ${LLMDBENCH_RUN_WORKSPACE_DIR}/vllm/
cp -f ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
en=$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r .executable)

# Wait for vLLM endpoint to be ready before running benchmark
ENDPOINT_URL=$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r '.["base-url"]')
if [[ -n "$ENDPOINT_URL" && "$ENDPOINT_URL" != "null" ]]; then
  MAX_WAIT=60
  INTERVAL=10
  echo "Waiting for vLLM endpoint at ${ENDPOINT_URL}/v1/models ..."
  for i in $(seq 1 $MAX_WAIT); do
    if curl -sf -o /dev/null --max-time 5 "${ENDPOINT_URL}/v1/models" 2>/dev/null; then
      echo "vLLM endpoint is ready (attempt $i/${MAX_WAIT})"
      break
    fi
    if [[ $i -eq $MAX_WAIT ]]; then
      echo "ERROR: vLLM endpoint not ready after ${MAX_WAIT} attempts ($((MAX_WAIT * INTERVAL))s)"
      exit 1
    fi
    echo "Attempt $i/${MAX_WAIT}: endpoint not ready, retrying in ${INTERVAL}s..."
    sleep $INTERVAL
  done
fi

echo "Running warmup with 3 prompts"
vllm bench serve --$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^ ^g' -e 's^=none$^^g' -e 's^num-prompts=[0-9]*^num-prompts=3^')  --seed $(date +%s) > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
echo "Running main benchmark"
export LLMDBENCH_HARNESS_ARGS="--$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} \
  | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' \
  | sed -e 's^=none ^ ^g' -e 's^=none$^^g') --seed $(date +%s) --save-result"
start=$(date +%s.%N)
vllm bench serve $LLMDBENCH_HARNESS_ARGS > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)
find ${LLMDBENCH_RUN_WORKSPACE_DIR}/vllm -maxdepth 1 -mindepth 1 -name '*.json' -exec mv -t "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"/ {} +

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION=$(cd /workspace/vllm; git rev-parse HEAD)

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
