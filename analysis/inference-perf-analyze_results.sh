#!/usr/bin/env bash
mkdir -p $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis
sleep 60
tm=$(date)
inference-perf --analyze "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
ec=$?
find $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR -type f -newermt "${tm}" -exec mv -t "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"/analysis {} +
exit $ec