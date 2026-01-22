#!/usr/bin/env bash

echo Using experiment result dir: "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
pushd "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" > /dev/null  2>&1
export LLMDBENCH_HARNESS_ARGS="--scenario ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} --output-path ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/results.json --disable-progress"
start=$(date +%s.%N)
guidellm benchmark $LLMDBENCH_HARNESS_ARGS > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION=$(guidellm --version)

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

# Convert results into universal format
export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=0
echo "Converting results.json to Benchmark Report v0.1"
benchmark-report $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json -b 0.1 -w guidellm $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_results.json.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
rc=$?
# Report errors but don't quit
if [[ $rc -ne 0 ]]; then
  echo "benchmark-report returned with error $rc"
  export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
fi

echo "Converting results.json to Benchmark Report v0.2"
benchmark-report $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json -b 0.2 -w guidellm $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report_v0.2,_results.json.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
rc=$?
# Report errors but don't quit
if [[ $rc -ne 0 ]]; then
  echo "benchmark-report returned with error $rc"
  export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
fi

if [[ $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC -ne 0 ]]; then
  echo "Results data conversion completed with errors."
  exit $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC
fi
echo "Results data conversion completed successfully."
