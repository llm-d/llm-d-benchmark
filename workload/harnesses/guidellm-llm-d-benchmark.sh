#!/usr/bin/env bash

echo Using experiment result dir: "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
pushd "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" > /dev/null  2>&1
guidellm benchmark --scenario "${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}" --output-path "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/results.json" --disable-progress > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

# Convert results into universal format
echo "Converting results.json"
benchmark-report $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json -w guidellm $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_results.json.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$?
if [[ $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC -ne 0 ]]; then
  echo "benchmark-report returned with error $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC
fi
echo "Results data conversion completed successfully."
