#!/usr/bin/env bash

inference-perf --config_file /workspace/llmdbench_workload.yaml
mkdir -p "/requests/$LLMDBENCH_HARNESS_STACK_NAME"
find /workspace -name '*.json' -exec mv -t "/requests/$LLMDBENCH_HARNESS_STACK_NAME"/ {} +
exit 0