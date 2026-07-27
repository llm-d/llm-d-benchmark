#!/usr/bin/env bash
# Run lm-evaluation-harness against a deployed llm-d endpoint.
#
# Ported from llm-d/llm-d#1683 (helpers/accuracy/run-lm-eval.sh).
# Port-forward / kubectl service-discovery removed: the llm-d-benchmark
# framework already detects the endpoint and injects
# LLMDBENCH_HARNESS_STACK_ENDPOINT_URL before invoking this script.
#
# Profile keys (read via yq from the mounted .yaml.in-rendered profile):
#   evaluation.tasks        – comma-separated lm-eval tasks (required)
#   evaluation.num_fewshot  – few-shot examples per task (default: 0)
#   evaluation.limit        – per-task sample cap; absent/null = full run
#   evaluation.num_concurrent – lm-eval client concurrency (default: 4)
#   evaluation.max_gen_toks – max generated tokens per request (optional)
#
# Environment overrides (take precedence over the profile file; pass their
# names to llmdbenchmark with `-g`, for example `-g TASKS,LIMIT`):
#   TASKS, NUM_FEWSHOT, LIMIT, NUM_CONCURRENT, MAX_GEN_TOKS, LM_EVAL_BASE_URL
#   (standard harness vars: LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR,
#    LLMDBENCH_RUN_WORKSPACE_DIR, LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME,
#    LLMDBENCH_DEPLOY_CURRENT_MODEL, LLMDBENCH_HARNESS_STACK_ENDPOINT_URL)

set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Set up results directory
# ---------------------------------------------------------------------------
echo "Using experiment result dir: ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
mkdir -p "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"

# ---------------------------------------------------------------------------
# 2. Dependency checks
# ---------------------------------------------------------------------------
if ! command -v yq >/dev/null 2>&1; then
  echo "ERROR: 'yq' (v4+) not found. Install it before running this harness." >&2
  exit 1
fi

if ! command -v lm_eval >/dev/null 2>&1; then
  echo "'lm_eval' not found -- installing lm-eval[api] at runtime..." >&2
  pip install --root-user-action=ignore "lm-eval[api]==0.4.12" >&2 || {
    echo "ERROR: failed to install lm-eval. Install with: pip install 'lm-eval[api]' transformers" >&2
    exit 1
  }
fi

# ---------------------------------------------------------------------------
# 3. Load profile config (YAML rendered by the framework from the .yaml.in)
# ---------------------------------------------------------------------------
PROFILE_FILE="${LLMDBENCH_RUN_WORKSPACE_DIR}/profiles/lm-eval/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}"
if [[ ! -f "${PROFILE_FILE}" ]]; then
  echo "ERROR: profile not found: ${PROFILE_FILE}" >&2
  exit 1
fi

cfg_get() {
  # Read a scalar from the profile; treat yq "null" (missing key) as empty.
  local v
  v="$(yq "$1" "${PROFILE_FILE}" 2>/dev/null)"
  [[ "${v}" == "null" ]] && v=""
  printf '%s' "${v}"
}

# Config keys -> shell vars; environment values override profile values.
MODEL="${MODEL:-${LLMDBENCH_DEPLOY_CURRENT_MODEL:-}}"
TASKS="${TASKS:-$(yq '.evaluation.tasks // [] | join(",")' "${PROFILE_FILE}" 2>/dev/null)}"
NUM_FEWSHOT="${NUM_FEWSHOT:-$(cfg_get '.evaluation.num_fewshot')}"
NUM_FEWSHOT="${NUM_FEWSHOT:-0}"
MAX_GEN_TOKS="${MAX_GEN_TOKS:-$(cfg_get '.evaluation.max_gen_toks')}"
NUM_CONCURRENT="${NUM_CONCURRENT:-$(cfg_get '.evaluation.num_concurrent')}"
NUM_CONCURRENT="${NUM_CONCURRENT:-4}"

# LIMIT is special: an explicit empty LIMIT means "full run";
# distinguish "set (even if empty)" from "unset".
if [[ -n "${LIMIT+x}" ]]; then
  LIMIT="${LIMIT}"
else
  LIMIT="$(cfg_get '.evaluation.limit')"
fi

# ---------------------------------------------------------------------------
# 4. Validate required values
# ---------------------------------------------------------------------------
if [[ -z "${MODEL}" ]]; then
  echo "ERROR: model not set in LLMDBENCH_DEPLOY_CURRENT_MODEL or MODEL env" >&2
  exit 1
fi
if [[ -z "${TASKS}" ]]; then
  echo "ERROR: tasks not set in profile (.evaluation.tasks) or TASKS env" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 5. Build lm_eval arguments
# ---------------------------------------------------------------------------
BASE_URL="${LM_EVAL_BASE_URL:-${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:-}}"
# Ensure the URL ends with /v1/completions (lm-eval local-completions needs the full path)
if [[ -n "${BASE_URL}" && "${BASE_URL}" != */v1/completions ]]; then
  BASE_URL="${BASE_URL%/}/v1/completions"
fi

if [[ -z "${BASE_URL}" ]]; then
  echo "ERROR: LLMDBENCH_HARNESS_STACK_ENDPOINT_URL is not set" >&2
  exit 1
fi

MODEL_ARGS="base_url=${BASE_URL},model=${MODEL},tokenizer_backend=huggingface,tokenized_requests=False,num_concurrent=${NUM_CONCURRENT},max_retries=3"
if [[ -n "${MAX_GEN_TOKS}" ]]; then
  MODEL_ARGS="${MODEL_ARGS},max_gen_toks=${MAX_GEN_TOKS}"
fi

LIMIT_ARG=()
if [[ -n "${LIMIT}" ]]; then
  LIMIT_ARG=(--limit "${LIMIT}")
fi

HARNESS_ARGS=(
  --model local-completions
  --model_args "${MODEL_ARGS}"
  --tasks "${TASKS}"
  --num_fewshot "${NUM_FEWSHOT}"
  --batch_size 1
  "${LIMIT_ARG[@]}"
  --output_path "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
)
export LLMDBENCH_HARNESS_ARGS="${HARNESS_ARGS[*]}"

# ---------------------------------------------------------------------------
# 6. Log run parameters
# ---------------------------------------------------------------------------
echo "Running lm_eval:"
echo "  base_url:    ${BASE_URL}"
echo "  model:       ${MODEL}"
echo "  tasks:       ${TASKS}"
echo "  num_fewshot: ${NUM_FEWSHOT}"
echo "  limit:       ${LIMIT:-<full>}"
echo "  max_gen_toks:${MAX_GEN_TOKS:-<default>}"
echo "  concurrency: ${NUM_CONCURRENT}"
echo "  output:      ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
echo

# ---------------------------------------------------------------------------
# 7. Execute lm_eval – capture stdout/stderr, record timing
# ---------------------------------------------------------------------------
start=$(date +%s.%N)
set +e
lm_eval "${HARNESS_ARGS[@]}" \
  > >(tee -a "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/stdout.log") \
  2> >(tee -a "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/stderr.log" >&2)
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC=$?
set -e
stop=$(date +%s.%N)

export LLMDBENCH_HARNESS_START=$(date -d "@${start}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_STOP=$(date -d "@${stop}" --iso-8601=seconds)
export LLMDBENCH_HARNESS_DELTA=PT$(echo "$stop - $start" | bc)S
export LLMDBENCH_HARNESS_VERSION=$(pip show lm-eval 2>/dev/null | awk '/^Version:/{print $2}' || echo 'unknown')

# ---------------------------------------------------------------------------
# 8. Write run metadata (same schema as other harnesses)
# ---------------------------------------------------------------------------
cat > "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/run_metadata.yaml" <<METADATA
harness_start: "${LLMDBENCH_HARNESS_START}"
harness_stop: "${LLMDBENCH_HARNESS_STOP}"
harness_delta: "${LLMDBENCH_HARNESS_DELTA}"
harness_args: "${LLMDBENCH_HARNESS_ARGS}"
harness_version: "${LLMDBENCH_HARNESS_VERSION}"
harness_name: "lm-eval"
harness_workload: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME:-}"
harness_rc: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
model: "${MODEL}"
endpoint_url: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:-}"
tasks: "${TASKS}"
num_fewshot: "${NUM_FEWSHOT}"
limit: "${LIMIT:-}"
num_concurrent: "${NUM_CONCURRENT}"
METADATA
echo "Run metadata written to ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/run_metadata.yaml"

# ---------------------------------------------------------------------------
# 9. Exit with harness return code
# ---------------------------------------------------------------------------
if [[ ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC} -ne 0 ]]; then
  echo "Harness returned with error ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}"
  exit ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}
fi

echo "Done. Results: ${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
exit ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_RC}
