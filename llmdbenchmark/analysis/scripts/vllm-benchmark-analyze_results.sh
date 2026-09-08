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
python3 /usr/local/bin/extract_summary.py "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" "Result =="
_summary_rc=$?

# Integrate vLLM metrics into benchmark report(s) v0.2 and generate plots
_metrics_dir="$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics"
if [[ -f "$_metrics_dir/processed/metrics_summary.json" ]]; then
  # Via the shared module, not an inline one-liner: it clips each stage report to
  # that stage's own window, which the one-liner could not do.
  echo "Integrating metrics summary into benchmark report(s) v0.2..."
  python3 /usr/local/bin/embed_metrics.py "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" 2>&1 | tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log" || true
  echo "Generating metric plots..."
  # Both paths: the report's graph_path field cites metrics/graphs/ (the
  # default), while the driver pass this replaces wrote analysis/graphs/ and
  # sync_analysis_dir lifts that one into the workspace. Writing only one
  # would change the tree an uncompressed run used to produce.
  python3 /usr/local/bin/visualize_metrics.py "$_metrics_dir" 2>&1 | tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log" || true
  python3 /usr/local/bin/visualize_metrics.py "$_metrics_dir" \
    -o "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analysis/graphs" 2>&1 | tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log" || true
fi

# Outside the metrics guard: these read the harness result files, not the
# Prometheus snapshots, so they apply whether or not metrics were collected.
echo "Generating per-request and session plots..."
python3 /usr/local/bin/generate_plots.py "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" 2>&1 | tee -a "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log" || true

exit $_summary_rc
