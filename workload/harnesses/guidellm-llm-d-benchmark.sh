#!/usr/bin/env bash
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
cd /workspace/guidellm/
guidellm benchmark --$(cat /workspace/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | grep -v "^executable" | yq -r 'to_entries | map("\(.key)=\(.value)") | join(" --")' | sed -e 's^=none ^^g' -e 's^=none$^^g') --output-path=$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC