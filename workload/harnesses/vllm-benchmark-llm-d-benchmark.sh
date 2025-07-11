#!/usr/bin/env bash
mkdir -p "/requests/$LLMDBENCH_HARNESS_STACK_NAME"
cd /workspace/vllm-benchmark/
en=$(cat /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r .executable)
python benchmarks/${en} --$(cat /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^^g')  --seed $(date +%s) --save-result > >(tee -a /requests/$LLMDBENCH_HARNESS_STACK_NAME/stdout.log) 2> >(tee -a /requests/$LLMDBENCH_HARNESS_STACK_NAME/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC