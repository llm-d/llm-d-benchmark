#!/usr/bin/env bash
mkdir -p "$LLMDBENCH_HARNESS_RESULTS_DIR"
cd /workspace/vllm-benchmark/
en=$(cat /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r .executable)
python benchmarks/${en} --$(cat /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^^g' -e 's^=none$^^g')  --seed $(date +%s) --save-result > >(tee -a $LLMDBENCH_HARNESS_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_HARNESS_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
find /workspace -name '*.json' -exec mv -t "$LLMDBENCH_HARNESS_RESULTS_DIR"/ {} +
exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC