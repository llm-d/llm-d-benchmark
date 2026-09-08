#!/usr/bin/env bash

# Helpers for exposing inference-perf lifecycle boundaries to campaign
# orchestrators. This file is sourced by inference-perf-llm-d-benchmark.sh.

inference_perf_final_stage_id() {
  local config_file="$1" stage_count

  stage_count="$(yq '.load.stages | length' "${config_file}" 2>/dev/null)" || return 0
  if [[ "${stage_count}" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "$((stage_count - 1))"
  fi
}

inference_perf_capture_stream() {
  local results_dir="$1" log_name="$2" final_stage_id="$3" experiment_id="$4"
  local line marker_tmp safe_experiment

  safe_experiment="${experiment_id//\\/\\\\}"
  safe_experiment="${safe_experiment//\"/\\\"}"

  while IFS= read -r line || [[ -n "${line}" ]]; do
    printf '%s\n' "${line}" | tee -a "${results_dir}/${log_name}"
    # Session-replay stages log "session-based run completed"; rate-based ones
    # log "run completed". Matches metrics_embed.py's STAGE_MARKER_RE.
    if [[ -n "${final_stage_id}" && ( \
        "${line}" == *"Stage ${final_stage_id} - run completed"* || \
        "${line}" == *"Stage ${final_stage_id} - session-based run completed"* ) ]] && \
        mkdir "${results_dir}/.traffic_complete.lock" 2>/dev/null; then
      marker_tmp="${results_dir}/.traffic_complete.yaml.$$"
      printf 'schema_version: "1"\nevent: "traffic_complete"\nexperiment_id: "%s"\nfinal_stage_id: %s\ncompleted_at: "%s"\n' \
        "${safe_experiment}" "${final_stage_id}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "${marker_tmp}"
      mv "${marker_tmp}" "${results_dir}/traffic_complete.yaml"
      printf 'LLMDBENCH_EVENT_V1 traffic_complete experiment_id=%s final_stage_id=%s\n' \
        "${experiment_id}" "${final_stage_id}" | tee -a "${results_dir}/stdout.log"
    fi
  done
}
