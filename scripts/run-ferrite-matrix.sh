#!/usr/bin/env bash
# Run the ferrite-vs-python comparison matrix.
#
# Loops over (scenario x profile) cells from the manifest and invokes
# llmdbenchmark for each. Standup happens once per scenario; profiles iterate
# via -w / --workload, which reuses the deployed pod.
#
# Usage:
#   scripts/run-ferrite-matrix.sh --tier tier1 --scenario ferrite-7b --ns my-ns
#   scripts/run-ferrite-matrix.sh --tier tier2 --scenario ferrite-7b --ns my-ns
#   scripts/run-ferrite-matrix.sh --tier tier1 --all-fp16-scenarios --ns my-ns
#   scripts/run-ferrite-matrix.sh --tier tier3 --scenario ferrite-7b-fp8 --ns my-ns-fp8
#
# Flags:
#   --tier {tier1|tier2|tier3}    which subset of cells to run
#   --scenario <name>             one scenario at a time, OR
#   --all-fp16-scenarios          shorthand for "ferrite-3b,ferrite-7b,ferrite-14b"
#   --ns <namespace>              k8s namespace (passed as -p to llmdbenchmark)
#   --workspace <dir>             workspace dir for results (default: ~/data/ferrite-matrix-<scenario>)
#   --image-repo <repo>           override images.vllm.repository (e.g. for ferrite swap)
#   --image-tag <tag>             override images.vllm.tag
#   --skip-standup                reuse existing standup -- only run profiles
#   --dry-run                     print commands without executing
#   --quiet                       suppress llmdbenchmark stderr noise; only show ours
#
# Exit codes:
#   0    all cells succeeded
#   1    bad CLI args
#   2    one or more cells failed (errors collected, run continues)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${REPO_ROOT}/workload/profiles/vllm-benchmark/ferrite-matrix-manifest.yaml"

usage() {
  sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

TIER=""
SCENARIO=""
ALL_FP16=false
NS=""
WORKSPACE=""
IMAGE_REPO=""
IMAGE_TAG=""
SKIP_STANDUP=false
DRY_RUN=false
QUIET=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier)               TIER="$2"; shift 2 ;;
    --scenario)           SCENARIO="$2"; shift 2 ;;
    --all-fp16-scenarios) ALL_FP16=true; shift ;;
    --ns)                 NS="$2"; shift 2 ;;
    --workspace)          WORKSPACE="$2"; shift 2 ;;
    --image-repo)         IMAGE_REPO="$2"; shift 2 ;;
    --image-tag)          IMAGE_TAG="$2"; shift 2 ;;
    --skip-standup)       SKIP_STANDUP=true; shift ;;
    --dry-run)            DRY_RUN=true; shift ;;
    --quiet)              QUIET=true; shift ;;
    -h|--help)            usage 0 ;;
    *) echo "Unknown flag: $1" >&2; usage 1 ;;
  esac
done

[[ -z "${TIER}" ]] && { echo "ERROR: --tier required" >&2; usage 1; }
[[ -z "${NS}"   ]] && { echo "ERROR: --ns required"   >&2; usage 1; }
case "${TIER}" in tier1|tier2|tier3) ;; *) echo "ERROR: --tier must be tier1|tier2|tier3" >&2; usage 1 ;; esac

if $ALL_FP16; then
  [[ -n "${SCENARIO}" ]] && { echo "ERROR: pick one of --scenario or --all-fp16-scenarios" >&2; usage 1; }
  SCENARIOS=("ferrite-3b" "ferrite-7b" "ferrite-14b")
else
  [[ -z "${SCENARIO}" ]] && { echo "ERROR: --scenario or --all-fp16-scenarios required" >&2; usage 1; }
  SCENARIOS=("${SCENARIO}")
fi

# Pull the cell list from the manifest.  yq is already a dependency of the
# benchmark harness so it's safe to require here.
if ! command -v yq >/dev/null 2>&1; then
  echo "ERROR: yq not on PATH (needed to read ${MANIFEST})" >&2
  exit 1
fi
mapfile -t PROFILES < <(yq -r ".${TIER}[]" "${MANIFEST}")
[[ ${#PROFILES[@]} -eq 0 ]] && { echo "ERROR: no profiles for tier ${TIER}" >&2; exit 1; }

echo "============================================================"
echo "Ferrite matrix run"
echo "  Tier:        ${TIER} (${#PROFILES[@]} profiles)"
echo "  Scenarios:   ${SCENARIOS[*]}"
echo "  Namespace:   ${NS}"
[[ -n "${IMAGE_REPO}" ]] && echo "  Image:       ${IMAGE_REPO}:${IMAGE_TAG:-<unset>}"
$DRY_RUN && echo "  DRY-RUN: commands printed, not executed"
echo "============================================================"

# Build per-invocation -e overrides for image swap (ferrite vs python).
EXTRA_OVERRIDES=()
[[ -n "${IMAGE_REPO}" ]] && EXTRA_OVERRIDES+=(-e "images.vllm.repository=${IMAGE_REPO}")
[[ -n "${IMAGE_TAG}"  ]] && EXTRA_OVERRIDES+=(-e "images.vllm.tag=${IMAGE_TAG}")

declare -i FAILS=0
declare -i TOTAL=0
declare -A FAILED_CELLS=()

run_or_print() {
  local label="$1"; shift
  if $DRY_RUN; then
    printf "[DRY] %s\n  %s\n" "${label}" "$*"
  else
    echo "==> ${label}"
    if $QUIET; then
      "$@" 2>/dev/null || return $?
    else
      "$@"
    fi
  fi
}

for SC in "${SCENARIOS[@]}"; do
  WS="${WORKSPACE:-${HOME}/data/ferrite-matrix-${SC}}"
  echo
  echo "----- Scenario: ${SC} (workspace: ${WS}) -----"

  if ! $SKIP_STANDUP; then
    run_or_print "standup ${SC}" \
      llmdbenchmark --spec "guides/${SC}" --workspace "${WS}" \
        "${EXTRA_OVERRIDES[@]}" \
        standup -p "${NS}"
  fi

  for PROFILE in "${PROFILES[@]}"; do
    TOTAL+=1
    set +e
    run_or_print "run ${SC} / ${PROFILE}" \
      llmdbenchmark --spec "guides/${SC}" --workspace "${WS}" \
        "${EXTRA_OVERRIDES[@]}" \
        run -p "${NS}" -w "${PROFILE}"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
      FAILS+=1
      FAILED_CELLS["${SC}/${PROFILE}"]="$rc"
      echo "    FAILED (rc=${rc}) -- continuing matrix"
    fi
  done
done

echo
echo "============================================================"
echo "Matrix complete: ${TOTAL} cells, ${FAILS} failed"
if [[ ${FAILS} -gt 0 ]]; then
  echo "Failed cells:"
  for k in "${!FAILED_CELLS[@]}"; do
    echo "  ${k} (rc=${FAILED_CELLS[$k]})"
  done
fi
echo "============================================================"

[[ ${FAILS} -gt 0 ]] && exit 2 || exit 0
