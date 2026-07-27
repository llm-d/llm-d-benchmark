#!/usr/bin/env bash
# Analyze results for the lm-eval harness.
#
# The llm-d-benchmark launcher (build/llm-d-benchmark.sh) runs
# "{harness}-analyze_results.sh" after the main harness. lm-evaluation-harness
# already writes its own structured JSON (via --output_path), so this analyzer
# is intentionally light: it locates the lm_eval results JSON, prints a compact
# per-task accuracy summary, and copies the canonical results file to a
# predictable name for downstream tooling.
#
# Contract: consumes LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR, exits 0 on success.

set -uo pipefail

RESULTS_DIR="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR:-}"
if [[ -z "${RESULTS_DIR}" || ! -d "${RESULTS_DIR}" ]]; then
  echo "ERROR: LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR not set or missing: '${RESULTS_DIR}'" >&2
  exit 1
fi

echo "Analyzing lm-eval results in: ${RESULTS_DIR}"

# lm_eval writes results to {output_path}/**/results_*.json (or results.json).
RESULTS_JSON="$(find "${RESULTS_DIR}" -type f \( -name 'results_*.json' -o -name 'results.json' \) 2>/dev/null \
  | sort | tail -1)"

if [[ -z "${RESULTS_JSON}" ]]; then
  echo "WARNING: no lm_eval results JSON found under ${RESULTS_DIR}." >&2
  echo "         Check stdout.log / stderr.log for the harness run." >&2
  # Do not hard-fail the pipeline: the harness RC already reflects the run.
  exit 0
fi

echo "Found results file: ${RESULTS_JSON}"
# Expose a stable filename alongside the timestamped one.
cp -f "${RESULTS_JSON}" "${RESULTS_DIR}/lm-eval-results.json" 2>/dev/null || true

# Print a compact accuracy summary. Prefer python3 (present in the image).
if command -v python3 >/dev/null 2>&1; then
  python3 - "${RESULTS_JSON}" <<'PYEOF'
import json, sys

path = sys.argv[1]
try:
    with open(path) as fh:
        data = json.load(fh)
except Exception as exc:  # pragma: no cover
    print(f"WARNING: could not parse {path}: {exc}", file=sys.stderr)
    sys.exit(0)

results = data.get("results", {})
if not results:
    print("No 'results' section in lm_eval JSON.")
    sys.exit(0)

print("")
print("lm-eval accuracy summary")
print("-" * 48)
print(f"{'task':<24}{'metric':<14}{'value':>10}")
print("-" * 48)
for task, metrics in sorted(results.items()):
    for metric, value in metrics.items():
        metric_name = metric.split(",", 1)[0]
        if (
            metric_name in {"alias", "name", "sample_len", "sample_count"}
            or metric_name.endswith("_stderr")
            or not isinstance(value, (int, float))
        ):
            continue
        print(f"{task:<24}{metric:<14}{value:>10.4f}")
print("-" * 48)
PYEOF
fi

echo "lm-eval analysis complete."
exit 0
