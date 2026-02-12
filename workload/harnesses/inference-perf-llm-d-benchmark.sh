#!/usr/bin/env bash

echo Using experiment result dir: "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
pushd "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" > /dev/null  2>&1
yq '.storage["local_storage"]["path"] = '\"${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}\" <"${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/inference-perf/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}" -y >${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
export LLMDBENCH_HARNESS_ARGS="--config_file $(realpath ./${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME})"
start=$(date +%s.%N)
inference-perf $LLMDBENCH_HARNESS_ARGS > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION=$(cd /workspace/inference-perf; git rev-parse HEAD)

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
