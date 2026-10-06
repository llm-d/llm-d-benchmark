#!/usr/bin/env bash

# Convert results into universal format
export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=0
for result in $(find $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR -maxdepth 1 -name '*.json'); do
  result_fname=$(echo $result | rev | cut -d '/' -f 1 | rev)

  echo "Converting $result_fname to Benchmark Report v0.1"
  benchmark-report $result -b 0.1 -w inferencemax $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report,_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
  rc=$?
  # Report errors but don't quit
  if [[ $rc -ne 0 ]]; then
    echo "benchmark-report returned with error $rc converting: $result"
    export LLMDBENCH_RUN_EXPERIMENT_CONVERT_RC=$rc
  fi
  echo
  echo "Converting $result_fname to Benchmark Report v0.2"
  benchmark-report $result -b 0.2 -w inferencemax $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/benchmark_report_v0.2,_$result_fname.yaml 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
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
python3 /usr/local/bin/extract_summary.py "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" "Result =="
_summary_rc=$?

# Integrate vLLM metrics into benchmark report(s) v0.2
_metrics_dir="$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics"
if [[ -f "$_metrics_dir/processed/metrics_summary.json" ]]; then
  # Via the shared module, not an inline one-liner: it clips each stage report to
  # that stage's own window, which the one-liner could not do.
  echo "Integrating metrics summary into benchmark report(s) v0.2..."
  python3 /usr/local/bin/embed_metrics.py "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" 2>&1 | tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log" || true
fi

exit $_summary_rc
