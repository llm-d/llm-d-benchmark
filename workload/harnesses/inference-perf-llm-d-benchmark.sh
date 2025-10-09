#!/usr/bin/env bash

echo Using experiment result dir: "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
pushd "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
yq '.storage["local_storage"]["path"] = '\"${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}\" <"${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/inference-perf/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}" -y >${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
inference-perf --config_file "$(realpath ./${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME})" > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

# Convert results into universal format
for result in $(find $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR -maxdepth 1 -name 'stage_*.json'); do
  result_fname=$(echo $result | rev | cut -d '/' -f 1 | rev)
  convert.py $result -w inference-perf $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
  # Report errors but don't quit
  export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$?
  if [[ $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC -ne 0 ]]; then
    echo "convert.py returned with error $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC converting: $result"
  fi
done
