#!/usr/bin/env bash

# Placeholder, to be populated later.
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
