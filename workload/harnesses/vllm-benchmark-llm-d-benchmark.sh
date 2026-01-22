#!/usr/bin/env bash

mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
cd ${LLMDBENCH_RUN_WORKSPACE_DIR}/vllm/
cp -f ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
en=$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r .executable)
echo "Running warmup with 3 prompts"
vllm bench serve --$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^ ^g' -e 's^=none$^^g' -e 's^num-prompts=[0-9]*^num-prompts=3^')  --seed $(date +%s) > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
echo "Running main benchmark"
start=$(date +%s.%N)
vllm bench serve --$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/vllm-benchmark/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^ ^g' -e 's^=none$^^g')  --seed $(date +%s) --save-result > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)
find ${LLMDBENCH_RUN_WORKSPACE_DIR}/vllm -maxdepth 1 -mindepth 1 -name '*.json' -exec mv -t "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"/ {} +

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

# Convert results into universal format
# We can't easily determine what the result filename will be, so search for and
# convert all possibilities.
for result in $(find $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR -maxdepth 1 -name 'vllm*.json'); do
  echo "Converting $result"
  result_fname=$(echo $result | rev | cut -d '/' -f 1 | rev)
  benchmark-report $result -w vllm $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
  # Report errors but don't quit
  export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$?
  if [[ $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC -ne 0 ]]; then
    echo "benchmark-report returned with error $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC converting: $result"
  fi
done

echo "Results data conversion completed."
