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
python3 /usr/local/bin/extract_summary.py "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" "Setup complete, starting benchmarks"
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
