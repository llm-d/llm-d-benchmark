#!/usr/bin/env bash

mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis"
result_start=$(grep -nr "Result ==" $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | cut -d ':' -f 1)
total_file_lenght=$(cat $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | wc -l)
cat $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | sed "$result_start,$total_file_lenght!d" > $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis/summary.txt
exit $?
