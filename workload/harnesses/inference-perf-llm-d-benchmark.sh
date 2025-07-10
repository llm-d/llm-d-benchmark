#!/usr/bin/env bash

mkdir -p "/requests/$LLMDBENCH_HARNESS_STACK_NAME"
inference-perf --config_file /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} > >(tee -a /requests/$LLMDBENCH_HARNESS_STACK_NAME/stdout.log) 2> >(tee -a /requests/$LLMDBENCH_HARNESS_STACK_NAME/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
find /workspace -name '*.json' -exec mv -t "/requests/$LLMDBENCH_HARNESS_STACK_NAME"/ {} +
exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
