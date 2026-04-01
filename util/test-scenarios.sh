#!/usr/bin/env bash
set -o pipefail

# -----------------------------------------------------------------------
# test-scenarios.sh
#
# This script is for TESTING ONLY. It validates that scenario configs
# produce working deployments by running standup/teardown cycles against
# a live cluster. It does NOT run benchmarks or collect results.
#
# Use this to verify scenario changes before committing, or to validate
# that all scenarios work on a new cluster.
# -----------------------------------------------------------------------
#
# Usage:
#   ./util/test-scenarios.sh [options] [namespace]
#
# Options:
#   --stable       Run stable scenarios only (default)
#   --trouble      Run known-trouble scenarios only (wide-ep-lws, spyre)
#   --all          Run all scenario groups
#   --ms-only      Only test modelservice (skip standalone)
#   --sa-only      Only test standalone (skip modelservice)
#
# Examples:
#   ./util/test-scenarios.sh llm-d-vezio-ang
#   ./util/test-scenarios.sh --trouble llm-d-vezio-ang
#   ./util/test-scenarios.sh --all --ms-only llm-d-vezio-ang
#   ./util/test-scenarios.sh --stable --trouble llm-d-vezio-ang
#
# Note: CICD scenarios (cks, gke-h100, kind-sim, ocp) are not included.
# They require specific cluster infrastructure and are tested via GitHub Actions.

# Parse arguments
RUN_STABLE=false
RUN_TROUBLE=false
METHOD_FILTER=""  # empty = both, "ms" = modelservice only, "sa" = standalone only
NS=""

for arg in "$@"; do
  case "$arg" in
    --stable)  RUN_STABLE=true ;;
    --trouble) RUN_TROUBLE=true ;;
    --all)     RUN_STABLE=true; RUN_TROUBLE=true ;;
    --ms-only) METHOD_FILTER="ms" ;;
    --sa-only) METHOD_FILTER="sa" ;;
    --help|-h)
      sed -n '3,/^$/p' "$0" | sed 's/^# //' | sed 's/^#//'
      exit 0
      ;;
    -*)
      echo "Unknown option: $arg (use --help for usage)"
      exit 1
      ;;
    *)
      NS="$arg"
      ;;
  esac
done

# Default to stable if nothing selected
if ! $RUN_STABLE && ! $RUN_TROUBLE; then
  RUN_STABLE=true
fi

NS="${NS:-llm-d-vezio-ang}"
LOG_DIR="/tmp/llmdbench-test-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

# -----------------------------------------------------------------------
# Scenario groups
# -----------------------------------------------------------------------

# Stable: scenarios that support both modelservice and standalone
STABLE_BOTH=(
  examples/cpu
  examples/gpu
  examples/sim-small
  guides/simulated-accelerators
)

# Stable: modelservice-only well-lit paths
STABLE_MS_ONLY=(
  guides/inference-scheduling
  guides/pd-disaggregation
  guides/precise-prefix-cache-aware
  guides/tiered-prefix-cache
)

# Trouble: known issues (RDMA/HCA for wide-ep-lws, cluster-specific for spyre)
TROUBLE_BOTH=(
  examples/spyre
)

TROUBLE_MS_ONLY=(
  guides/wide-ep-lws
)

# -----------------------------------------------------------------------
# Test runner
# -----------------------------------------------------------------------

declare -a RESULTS=()

run_test() {
  local spec="$1"
  local method="$2"
  local group="$3"
  local label="${spec} (${method}) [${group}]"
  local safe_name="$(echo "${spec}-${method}" | tr '/' '-')"
  local standup_log="${LOG_DIR}/standup-${safe_name}.log"
  local teardown_log="${LOG_DIR}/teardown-${safe_name}.log"

  echo "=========================================="
  echo "Testing: ${label}"
  echo "=========================================="

  # Standup
  if [ "$method" = "standalone" ]; then
    llmdbenchmark --spec "$spec" standup -p "$NS" -t standalone 2>&1 | tee "$standup_log"
    STANDUP_EXIT=${PIPESTATUS[0]}
  else
    llmdbenchmark --spec "$spec" standup -p "$NS" 2>&1 | tee "$standup_log"
    STANDUP_EXIT=${PIPESTATUS[0]}
  fi

  if [ $STANDUP_EXIT -eq 0 ]; then
    echo "STANDUP PASSED: ${label}"
    RESULTS+=("PASS|${label}|standup")
  else
    echo "STANDUP FAILED: ${label} (exit code ${STANDUP_EXIT})"
    RESULTS+=("FAIL|${label}|standup")
  fi

  # Teardown (always run to clean up)
  if [ "$method" = "standalone" ]; then
    llmdbenchmark --spec "$spec" teardown -p "$NS" -t standalone 2>&1 | tee "$teardown_log"
    TEARDOWN_EXIT=${PIPESTATUS[0]}
  else
    llmdbenchmark --spec "$spec" teardown -p "$NS" 2>&1 | tee "$teardown_log"
    TEARDOWN_EXIT=${PIPESTATUS[0]}
  fi

  if [ $TEARDOWN_EXIT -eq 0 ]; then
    RESULTS+=("PASS|${label}|teardown")
  else
    echo "TEARDOWN FAILED: ${label} (exit code ${TEARDOWN_EXIT})"
    RESULTS+=("FAIL|${label}|teardown")
  fi

  echo ""
}

run_both() {
  local spec="$1"
  local group="$2"
  if [ "$METHOD_FILTER" != "sa" ]; then
    run_test "$spec" "modelservice" "$group"
  fi
  if [ "$METHOD_FILTER" != "ms" ]; then
    run_test "$spec" "standalone" "$group"
  fi
}

run_ms_only() {
  local spec="$1"
  local group="$2"
  if [ "$METHOD_FILTER" = "sa" ]; then
    echo "Skipping ${spec} -- modelservice only, but --sa-only requested"
    return
  fi
  run_test "$spec" "modelservice" "$group"
}

# -----------------------------------------------------------------------
# Execution
# -----------------------------------------------------------------------

# Build description of what we're running
groups=""
$RUN_STABLE && groups="${groups}stable "
$RUN_TROUBLE && groups="${groups}trouble "

echo "=========================================="
echo "Test Suite: Standup/Teardown Validation"
echo "Namespace:  ${NS}"
echo "Groups:     ${groups}"
echo "Methods:    ${METHOD_FILTER:-both}"
echo "Log dir:    ${LOG_DIR}"
echo "=========================================="
echo ""

if $RUN_STABLE; then
  echo "--- STABLE scenarios ---"
  for spec in "${STABLE_BOTH[@]}"; do run_both "$spec" "stable"; done
  for spec in "${STABLE_MS_ONLY[@]}"; do run_ms_only "$spec" "stable"; done
fi

if $RUN_TROUBLE; then
  echo "--- TROUBLE scenarios (known issues) ---"
  for spec in "${TROUBLE_BOTH[@]}"; do run_both "$spec" "trouble"; done
  for spec in "${TROUBLE_MS_ONLY[@]}"; do run_ms_only "$spec" "trouble"; done
fi

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
echo ""
echo "=========================================="
echo "RESULTS SUMMARY"
echo "=========================================="

pass_count=0
fail_count=0

for entry in "${RESULTS[@]}"; do
  status="${entry%%|*}"
  rest="${entry#*|}"
  label="${rest%%|*}"
  phase="${rest#*|}"

  if [ "$status" = "PASS" ]; then
    echo "  PASS  ${label} (${phase})"
    ((pass_count++))
  else
    echo "  FAIL  ${label} (${phase})"
    ((fail_count++))
  fi
done

echo ""
echo "Total: $((pass_count + fail_count)) tests, ${pass_count} passed, ${fail_count} failed"
echo "Logs:  ${LOG_DIR}"
echo "=========================================="

# Exit with failure if any test failed
[ $fail_count -eq 0 ]
