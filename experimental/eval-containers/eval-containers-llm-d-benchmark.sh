#!/usr/bin/env bash
#
# eval-containers harness: runs ONE eval-containers task per parallel harness pod
# against the deployed llm-d endpoint, so a benchmark fans out with -j <#tasks>.
# Set harness.entrypoint to this script -- the eval image has no load-generator
# entrypoint of its own.
#
set -euo pipefail

# --- which task does this pod run? -------------------------------------------
# llm-d-benchmark fans -j N pods over a treatment and gives each pod its own
# results dir suffixed _<idx> (1..N); there is no per-pod index env var, so
# recover the 1-based index from that suffix. eval-containers task ids are
# 0-based. EVAL_TASK_OFFSET runs a big dataset in capacity-sized waves
# (wave k: -j size, EVAL_TASK_OFFSET=k*size).
results_dir="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR:?must be set by llm-d-benchmark}"
mkdir -p "$results_dir"
if [[ -z "${EVAL_TASK_ID:-}" ]]; then
  idx="${LLMDBENCH_RUN_EXPERIMENT_PARALLEL_INDEX:-${results_dir##*_}}"
  # parallel_idx is 1-based (framework: range(1, N+1)); fall back to 1 for a
  # missing / non-numeric / zero suffix so EVAL_TASK_ID can never go negative.
  case "$idx" in ''|*[!0-9]*|0) idx=1 ;; esac

  # EVAL_TASK_LIST selects an ARBITRARY set of task ids: a comma-separated list
  # indexed by this pod's 1-based position. EVAL_TASK_OFFSET can only express a
  # contiguous range, and the aider-polyglot dataset is language-ORDERED (cpp
  # 0-25, go 26-64, java 65-111, javascript 112-160, python 161-194, rust
  # 195-224), so every contiguous slice is effectively single-language and its
  # score is not comparable to any other slice. A representative sample needs an
  # explicit list.
  if [[ -n "${EVAL_TASK_LIST:-}" ]]; then
    IFS=',' read -r -a _task_ids <<< "$EVAL_TASK_LIST"
    if (( idx > ${#_task_ids[@]} )); then
      echo "eval-containers: FATAL pod index $idx exceeds EVAL_TASK_LIST length ${#_task_ids[@]}" >&2
      exit 1
    fi
    _tid="${_task_ids[$(( idx - 1 ))]}"
    _tid="${_tid//[[:space:]]/}"
    case "$_tid" in ''|*[!0-9]*)
      echo "eval-containers: FATAL EVAL_TASK_LIST entry $idx is not a task id: '$_tid'" >&2
      exit 1 ;;
    esac
    export EVAL_TASK_ID="$_tid"
  else
    export EVAL_TASK_ID="$(( idx - 1 + ${EVAL_TASK_OFFSET:-0} ))"
  fi
fi

# --- point the eval's in-pod model gateway at the deployed llm-d endpoint -----
# Single-image (standalone) mode: leaving ANTHROPIC_BASE_URL unset makes the
# eval start its own otel+gateway+agent+verifier pipeline in this pod; that
# in-pod gateway reads its upstream from OPENAI_API_BASE + EVAL_MODEL, so the
# agent's LLM calls land on the deployed llm-d model.
endpoint="${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:?endpoint not provided}"
endpoint="${endpoint%/}"
# The in-pod gateway (bifrost) is an OpenAI-compatible client: it appends the
# `/v1/chat/completions` path itself, so its upstream base must be the ROOT, not
# `.../v1` (a `/v1` suffix would double to `.../v1/v1/...` -> upstream 404).
endpoint="${endpoint%/v1}"
export OPENAI_API_BASE="$endpoint"
export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-llm-d}"  # pragma: allowlist secret (placeholder; llm-d ignores it)
# EVAL_MODEL must be the BARE served model handle, with NO provider prefix.
# /opt/gateway/start documents it as "a BARE handle" and renders it into a
# bifrost routing rule that carries the provider SEPARATELY:
#   "targets": [{ "provider": "<wire>", "model": "<EVAL_MODEL>" }]
# So an `openai/` prefix here is not a provider selector -- it becomes part of
# the model NAME. bifrost then looks up `openai/<model>` in the OpenAI catalog,
# finds nothing, and returns 404 "The model ... does not exist" for every agent
# call: reward 0.0, empty agent stdout, harness rc 0. Silent green.
export EVAL_MODEL="${LLMDBENCH_DEPLOY_CURRENT_MODEL:?model not provided}"

echo "eval-containers: task=$EVAL_TASK_ID model=$EVAL_MODEL endpoint=$OPENAI_API_BASE"

# --- raise the in-pod gateway's per-request timeout --------------------------
# bifrost's built-in per-request timeout is 30s, which is SHORTER than a
# reasoning model's generation latency (the model emits a <think> block before
# the answer). Measured 2026-07-30 over two 20-task waves: 65/198 (33%) and
# 55/156 (35%) of gateway requests returned 504 with durations pinned at
# 30002-30064 ms -- a fixed client deadline, not model variance. A successful
# call landed at 26460 ms, i.e. 3.5s under the wire. No task with 3+ 504s ever
# passed, so this silently depresses the score rather than failing the run.
#
# bifrost names the fix in its own error body ("increase it by setting the
# default_request_timeout_in_seconds in the network_config"), and
# config.json.template already writes a network_config block per provider
# holding base_url + allow_private_network. /opt/gateway/start renders that
# template with plain sed and never parses the JSON, so an injected field
# passes straight through -- no image rebuild needed.
#
# Keyed on "allow_private_network": true, which appears in all three provider
# blocks (anthropic, openai, gemini), so it survives base_url differences.
# 600s is comfortably above the worst observed generation and still bounded
# well under EVAL_TIMEOUT, so a wedged request cannot outlive its own task.
gw_timeout="${EVAL_GATEWAY_TIMEOUT:-600}"
gw_template=/opt/gateway/data/config.json.template
# Saved copy lives under $HOME, not /tmp: /tmp is not guaranteed to survive
# (macOS has wiped it mid-run) and this file's whole job is to be there for
# the revert below, so a cleared /tmp would turn a loud-skip patch into a
# silent, unrevertable one.
gw_template_orig="${HOME:-/tmp}/eval-containers-gw-config.json.template.orig"
if [[ -f "$gw_template" && -w "$(dirname "$gw_template")" ]]; then
  if grep -q 'default_request_timeout_in_seconds' "$gw_template"; then
    echo "eval-containers: gateway template already sets a request timeout; leaving it alone"
  elif ! cp "$gw_template" "$gw_template_orig"; then
    # Without a saved copy we cannot revert, so patching would be
    # unguarded -- skip it and leave the provider default in place. This is
    # the same degrade-gracefully outcome as the "not present/writable"
    # branch below, just discovered one step later.
    echo "eval-containers: WARNING could not save gateway template backup to ${gw_template_orig}; skipping timeout patch, provider default applies" >&2
  else
    gw_restore() {
      if ! cp "$gw_template_orig" "$gw_template"; then
        echo "eval-containers: WARNING revert failed; ${gw_template} may be left in a half-patched state" >&2
      fi
    }
    if ! sed -i "s/\"allow_private_network\": true/\"allow_private_network\": true, \"default_request_timeout_in_seconds\": ${gw_timeout}/g" \
      "$gw_template"; then
      # A disk-full temp-file write or a permissions race after the -w
      # dirname check above can make sed itself fail. Restore rather than
      # leave a possibly half-patched template in place.
      echo "eval-containers: WARNING timeout patch sed failed; reverting" >&2
      gw_restore
    else
      n=$(grep -c 'default_request_timeout_in_seconds' "$gw_template") || n=0
      if [[ "$n" -eq 3 ]]; then
        echo "eval-containers: gateway request timeout set to ${gw_timeout}s for 3 providers"
        rm -f "$gw_template_orig"
      else
        # Restore rather than run with a half-patched template: a malformed
        # config.json makes bifrost fail to boot, which surfaces later as an
        # opaque startup failure rather than as this patch's fault.
        echo "eval-containers: WARNING timeout patch hit $n/3 providers; reverting" >&2
        gw_restore
      fi
    fi
  fi
else
  echo "eval-containers: note gateway template not present/writable; provider default applies"
fi

# --- run the eval ------------------------------------------------------------
# image ENTRYPOINT stages /app for EVAL_TASK_ID, then execs the pipeline.
# Wall-clock is recorded around the whole task because the OTel traces only
# cover LLM calls: a span-derived duration silently omits agent startup, tool
# execution, and grading, and a task that hangs outside its calls looks fast.
# Analysis needs the real task window to report task latency and time-to-first
# -call honestly.
start=$(date +%s)
rc=0
"${EVAL_CONTAINERS_ENTRYPOINT:-/entrypoint.sh}" \
  "${EVAL_CONTAINERS_RUN:-/usr/local/bin/run}" || rc=$?
stop=$(date +%s)

# --- hand results back to llm-d-benchmark's collector ------------------------
output_dir="${EVAL_OUTPUT_DIR:-/output}"
if [[ -d "$output_dir" ]]; then cp -a "$output_dir/." "$results_dir/"; fi
# Escape free text for the double-quoted YAML scalars below. Backslash first, or
# the quote escapes get double-escaped. Control characters are illegal in a
# double-quoted scalar at all, and an unparsable file loses every key in it, not
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
# harness_start / harness_stop / harness_delta use the same key names and
# ISO-8601 formats the other harnesses write, so existing readers need no special
# case. BusyBox date has no -d @<epoch>, hence the -r fallback.
_iso() { date -d "@$1" --iso-8601=seconds 2>/dev/null || date -r "$1" +%Y-%m-%dT%H:%M:%S%z; }
printf 'harness_name: eval-containers\nharness_rc: %s\ntask_id: %s\nmodel: %s\nexperiment_id: "%s"\ndescription_text: "%s"\ndescription_keywords: "%s"\nharness_start: "%s"\nharness_stop: "%s"\nharness_delta: "PT%sS"\n' \
  "$rc" "$EVAL_TASK_ID" "$EVAL_MODEL" "${LLMDBENCH_RUN_EXPERIMENT_ID:-}" \
  "$_description_text" "$_description_keywords" \
  "$(_iso "$start")" "$(_iso "$stop")" "$(( stop - start ))" \
  > "$results_dir/run_metadata.yaml"

exit "$rc"
