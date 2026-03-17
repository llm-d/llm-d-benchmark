#!/usr/bin/env bash

# Convert results into universal format
# We can't easily determine what the result filename will be, so search for and
# convert all possibilities.
export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=0
for result in $(find $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR -maxdepth 1 -name 'openai*.json'); do
  result_fname=$(echo $result | rev | cut -d '/' -f 1 | rev)

  echo "Converting $result_fname to Benchmark Report v0.1"
  benchmark-report $result -b 0.1 -w vllm-benchmark $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
  rc=$?

  # Report errors but don't quit
  if [[ $rc -ne 0 ]]; then
    echo "benchmark-report returned with error $rc converting: $result"
    export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
  fi
  echo
  echo "Converting $result_fname to Benchmark Report v0.2"
  benchmark-report $result -b 0.2 -w vllm-benchmark $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report_v0.2,_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
  rc=$?
  # Report errors but don't quit
  if [[ $rc -ne 0 ]]; then
    echo "benchmark-report returned with error $rc converting: $result"
    export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
  fi
done

if [[ $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC -ne 0 ]]; then
  echo "Results data conversion completed with errors."
  exit $LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC
fi
echo "Results data conversion completed successfully."

mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis"
result_start=$(grep -nr "Result ==" $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | tail -1 | cut -d ':' -f 1)
total_file_lenght=$(cat $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | wc -l)
cat $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log | sed "$result_start,$total_file_lenght!d" > $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis/summary.txt
exit $?
