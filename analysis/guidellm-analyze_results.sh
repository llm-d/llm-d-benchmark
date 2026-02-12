#!/usr/bin/env bash
# Convert results into universal format
export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=0
echo "Converting results.json to Benchmark Report v0.1"
benchmark-report $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json -b 0.1 -w guidellm $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_results.json.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
rc=$?
# Report errors but don't quit
if [[ $rc -ne 0 ]]; then
  echo "benchmark-report returned with error $rc"
  export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
fi
echo
echo "Converting results.json to Benchmark Report v0.2"
benchmark-report $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/results.json -b 0.2 -w guidellm $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report_v0.2,_results.json.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
rc=$?
# Report errors but don't quit
if [[ $rc -ne 0 ]]; then
  echo "benchmark-report returned with error $rc"
  export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
fi

if [[ $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC -ne 0 ]]; then
  echo "Results data conversion completed with errors."
  exit $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC
fi
echo "Results data conversion completed successfully."

mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis"
#result_start=$(grep -nr "Benchmarks Stats:" $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | cut -d ':' -f 1)
result_start=$(grep -nr "Setup complete, starting benchmarks" $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | cut -d ':' -f 1)
total_file_lenght=$(cat $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | wc -l)
cat $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | sed "$result_start,$total_file_lenght!d" > $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis/summary.txt
exit $?
