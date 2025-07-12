#!/usr/bin/env bash

mkdir -p "$LLMDBENCH_HARNESS_RESULTS_DIR"
inference-perf --config_file /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} > >(tee -a $LLMDBENCH_HARNESS_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_HARNESS_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
find /workspace -name '*.json' -exec mv -t "$LLMDBENCH_HARNESS_RESULTS_DIR"/ {} +
exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
