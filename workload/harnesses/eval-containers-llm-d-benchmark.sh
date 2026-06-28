#!/usr/bin/env bash
#
# eval-containers harness for llm-d-benchmark.
#
# Runs ONE eval-containers task per parallel harness pod against the deployed
# llm-d endpoint, so a whole agentic benchmark (aider-polyglot, gaia, ...) fans
# out as one isolated container per task with  -j <#tasks>.
#
# Plugs in additively: this script ships in the llmdbench-harness-scripts
# ConfigMap (mounted at /workspace/harnesses) and the harness pod image is set
# to a published evals/<benchmark>--<agent>-standalone bundle via
# images.benchmark in the scenario. No edit to the llm-d-benchmark framework is
# required -- set harness.entrypoint to this script so it runs in place of the
# default load-generator entrypoint (which is not present in the eval image).
#
#   ... run -c <scenario-with-eval-containers-harness> -j <#tasks>
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
  export EVAL_TASK_ID="$(( idx - 1 + ${EVAL_TASK_OFFSET:-0} ))"
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
export EVAL_MODEL="openai/${LLMDBENCH_DEPLOY_CURRENT_MODEL:?model not provided}"

echo "eval-containers: task=$EVAL_TASK_ID model=$EVAL_MODEL endpoint=$OPENAI_API_BASE"

# --- run the eval ------------------------------------------------------------
# /entrypoint.sh is the image's ENTRYPOINT: it stages /app for EVAL_TASK_ID
# (prompt, starter files, grader ground truth) and then execs the pipeline we
# pass it. EVAL_BENCHMARK and EVAL_AGENT are baked into the
# evals/<benchmark>--<agent> image, so the only axis this pod chooses is the task.
rc=0
"${EVAL_CONTAINERS_ENTRYPOINT:-/entrypoint.sh}" \
  "${EVAL_CONTAINERS_RUN:-/usr/local/bin/run}" || rc=$?

# --- hand results back to llm-d-benchmark's collector ------------------------
# The pipeline writes the reward + agent logs + OTel traces under /output.
output_dir="${EVAL_OUTPUT_DIR:-/output}"
[[ -d "$output_dir" ]] && cp -a "$output_dir/." "$results_dir/" 2>/dev/null || true
printf 'harness_name: eval-containers\nharness_rc: %s\ntask_id: %s\nmodel: %s\n' \
  "$rc" "$EVAL_TASK_ID" "$EVAL_MODEL" > "$results_dir/run_metadata.yaml"

exit "$rc"
