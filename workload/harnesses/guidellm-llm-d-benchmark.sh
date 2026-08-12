#!/usr/bin/env bash

echo Using experiment result dir: "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
mkdir -p "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR"
pushd "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR" > /dev/null  2>&1

# Guard: an unsupported/unavailable "profile" value (e.g. "replay", which is
# only on guidellm main and not yet in a PyPI release) is rejected by
# guidellm's own CLI validation, but that error gets swallowed during argument
# parsing and resurfaces as a misleading "Missing field '--target': Field
# required", sending operators off to debug the wrong thing. Check the
# requested profile against what the installed guidellm actually supports
# before invoking it, so the real cause is reported up front.
requested_profile=$(yq -r '.profile' ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME})
if [[ -n "$requested_profile" ]] && [[ "$requested_profile" != "null" ]]; then
  supported_profiles=$(python3 -c "
from guidellm.benchmark import ProfileType
from guidellm.scheduler import StrategyType
from guidellm.utils.typing import get_literal_vals
print(' '.join(sorted(get_literal_vals(ProfileType | StrategyType))))
" 2>/dev/null)
  if [[ -n "$supported_profiles" ]] && [[ " $supported_profiles " != *" $requested_profile "* ]]; then
    echo "ERROR: workload profile '${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}' requests profile '${requested_profile}', which the installed guidellm ($(guidellm --version 2>/dev/null)) does not support. Supported profiles: ${supported_profiles}. If '${requested_profile}' is a newer guidellm feature (e.g. 'replay'), the benchmark image's guidellm pin needs to be updated to a version/commit that supports it." >&2
    exit 1
  fi
fi

export LLMDBENCH_HARNESS_ARGS="--target $(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} | yq -r .target) --scenario ${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/guidellm/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME} --output-path ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/results.json --disable-progress"

# Start metrics collection in background if enabled
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]]; then
  echo "Starting metrics collection..."
  /usr/local/bin/collect_metrics.sh start >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1 &
  METRICS_COLLECTOR_PID=$!
  echo "Metrics collector started with PID: $METRICS_COLLECTOR_PID"
  echo "Metrics collection logs: $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log"
fi

start=$(date +%s.%N)
guidellm benchmark $LLMDBENCH_HARNESS_ARGS > >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stdout.log) 2> >(tee -a $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/stderr.log >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
stop=$(date +%s.%N)

# Stop metrics collection
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]] && [[ -n "${METRICS_COLLECTOR_PID:-}" ]]; then
  echo "Stopping metrics collection..."
  /usr/local/bin/collect_metrics.sh stop >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1
  wait $METRICS_COLLECTOR_PID 2>/dev/null || true

  # Process collected metrics
  echo "Processing collected metrics..."
  /usr/local/bin/collect_metrics.sh process >> $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/metrics_collection.log 2>&1

  echo "Metrics collection complete. Check metrics_collection.log for details."
fi

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION=$(guidellm --version)

# Write run metadata to a file so the analyzer can read it.
# Environment variables exported here are lost when this subshell exits,
# so the file serves as the handoff mechanism to the analysis phase.
# Escape free text for the double-quoted YAML scalars below. Backslash first, or
# the quote escapes get double-escaped. Control characters are illegal in a
# double-quoted scalar at all, and an unparseable file loses every key in it, not
# just this one.
_yaml_escape() {
  local text="${1:-}" out="" index character
  text="${text//\\/\\\\}"
  text="${text//\"/\\\"}"
  text="${text//$'\t'/\\t}"
  text="${text//$'\n'/\\n}"
  for (( index=0; index<${#text}; index++ )); do
    character="${text:index:1}"
    if [[ "$character" == [[:cntrl:]] ]]; then
      printf -v character '\\x%02x' "'$character"
    fi
    out+="$character"
  done
  printf '%s' "$out"
}
_description_text="$(_yaml_escape "${LLMDBENCH_DESCRIPTION_TEXT:-}")"
_description_keywords="$(_yaml_escape "${LLMDBENCH_DESCRIPTION_KEYWORDS:-}")"
cat > "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/run_metadata.yaml" <<METADATA
harness_start: "${LLMDBENCH_HARNESS_START}"
harness_stop: "${LLMDBENCH_HARNESS_STOP}"
harness_delta: "${LLMDBENCH_HARNESS_DELTA}"
harness_args: "${LLMDBENCH_HARNESS_ARGS}"
harness_version: "${LLMDBENCH_HARNESS_VERSION}"
harness_name: "${LLMDBENCH_HARNESS_NAME:-guidellm}"
harness_workload: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME:-}"
harness_rc: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
experiment_id: "${LLMDBENCH_RUN_EXPERIMENT_ID:-}"
model: "${LLMDBENCH_DEPLOY_CURRENT_MODEL:-}"
endpoint_url: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:-}"
namespace: "${LLMDBENCH_VLLM_COMMON_NAMESPACE:-}"
description_text: "${_description_text}"
description_keywords: "${_description_keywords}"
METADATA
echo "Run metadata written to $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/run_metadata.yaml"

# If benchmark harness returned with an error, exit here
if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC -ne 0 ]]; then
  echo "Harness returned with error $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC"
  exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
fi
echo "Harness completed successfully."

exit $LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC
