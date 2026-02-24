#!/usr/bin/env bash

# Convert results into universal format
export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=0
for result in $(find $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR -maxdepth 1 -name 'stage_*.json'); do
  result_fname=$(echo $result | rev | cut -d '/' -f 1 | rev)

  echo "Converting $result_fname to Benchmark Report v0.1"
  benchmark-report $result -b 0.1 -w inference-perf $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
  rc=$?
  # Report errors but don't quit
  if [[ $rc -ne 0 ]]; then
    echo "benchmark-report returned with error $rc converting: $result"
    export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
  fi
  echo
  echo "Converting $result_fname to Benchmark Report v0.2"
  benchmark-report $result -b 0.2 -w inference-perf $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report_v0.2_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
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

mkdir -p $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis
sleep 60
tm=$(date)
inference-perf --analyze "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
ec=$?
find $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR -type f -newermt "${tm}" -exec mv -t "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"/analysis {} +
exit $ec
