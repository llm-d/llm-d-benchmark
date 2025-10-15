#!/usr/bin/env bash
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
cd ${LLMDBENCH_RUN_WORKSPACE_DIR}/guidellm/
cp -f ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
guidellm benchmark --$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^^g' -e 's^=none$^^g') --output-path=$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

# Convert results into universal format
convert.py $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json -w guidellm $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_results.json.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$?
if [[ $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC -ne 0 ]]; then
  echo "convert.py returned with error $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC
fi
echo "Results data conversion completed successfully."
