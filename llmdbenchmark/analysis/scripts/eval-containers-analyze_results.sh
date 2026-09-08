#!/usr/bin/env bash
#
# Analyze results for the eval-containers agentic harness.
#
# This harness had no analyzer, so its v0.2 report was the one report built only on
# the driver -- which meant it had to read the collected result set, and broke once
# that set was compressed. Building it here puts it with every other harness: the
# pod produces the report, collection just moves it.
#
# Contract: consumes LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR, exits 0 on success.

set -uo pipefail

RESULTS_DIR="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR:-}"
if [[ -z "${RESULTS_DIR}" || ! -d "${RESULTS_DIR}" ]]; then
  echo "ERROR: LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR not set or missing: '${RESULTS_DIR}'" >&2
  exit 1
fi

# One task per pod, so one result file. v0.2 only: the agentic schema has no v0.1
# equivalent, and the converter is reached through the Python API rather than the
# generic `benchmark-report -w` writer path.
RESULT="${RESULTS_DIR}/task/result.json"
if [[ ! -f "${RESULT}" ]]; then
  echo "WARNING: no task/result.json under ${RESULTS_DIR}; nothing to convert." >&2
  exit 0
fi

echo "Converting task/result.json to Benchmark Report v0.2"
python3 - "${RESULT}" "${RESULTS_DIR}/benchmark_report_v0.2,_result.json.yaml" <<'PYEOF'
import sys

try:
    from benchmark_report.native_to_br0_2 import import_eval_containers
except ImportError:
    from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
        import_eval_containers,
    )

result_file, output_file = sys.argv[1], sys.argv[2]
import_eval_containers(result_file).export_yaml(output_file)
print(f"Wrote {output_file}")
PYEOF
rc=$?
if [[ $rc -ne 0 ]]; then
  echo "benchmark report conversion returned with error $rc" >&2
  exit $rc
fi

mkdir -p "${RESULTS_DIR}/analysis"
python3 /usr/local/bin/extract_summary.py "${RESULTS_DIR}"

echo "eval-containers analysis complete."
exit 0
