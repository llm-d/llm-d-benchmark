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

# --- preserve the grader's test output (opt-in: EVAL_GRADE_VERBOSE=1) ---------
# The image's /grade.sh discards the entire test phase:
#
#   if timeout 120 sh -c "$TEST_CMD" >/dev/null 2>&1; then
#
# so a failed task leaves ONLY "0.0" in logs/verifier/reward.txt -- no compiler
# error, no failing assertion, no stack trace. A zero is then undiagnosable:
# "wrong answer", "solution written to the wrong path", and "toolchain missing
# from the image" are indistinguishable, and all three produce uniform zeros.
#
# It also logs grade.sh's SOLUTION COPY. grade.sh copies each solution file from
# /app/$f and skips it SILENTLY when absent (`if [ -f "$src" ]`), so if the agent
# wrote elsewhere the suite grades the untouched STUB and every task scores 0.0
# no matter how good the solution was -- invisible in the shipped script.
#
# It also copies each solution file to logs/verifier/solution/. The agent writes
# into /app and the graded copy lands in $EXERCISE_DIR, both inside the container
# -- so once the pod exits, the code that was actually graded is gone and only
# the compiler's complaint about it survives. That made one class of failure
# undiagnosable: a Go task failed with "syntax error: non-declaration statement
# outside function body" at lines 15 and 76, which is what raw markdown fences or
# prose in a .go file look like -- but with the file gone and the agent's stdout
# showing no fences, the cause could not be established either way.
#
# Grading semantics are unchanged: same TEST_CMD, same 120s timeout, same
# 1.0/0.0 from the same exit status to the same path. Only the output is kept.
# Guarded by `|| true` throughout: a patch failure must never fail the eval, and
# an image whose grade.sh has changed shape is left strictly alone.
if [[ "${EVAL_GRADE_VERBOSE:-0}" == "1" && -f /grade.sh ]]; then
  cp -a /grade.sh /grade.sh.orig 2>/dev/null || true
  python3 - <<'PY' || echo "eval-containers: grade.sh patch skipped (see above)" >&2
import sys
p = "/grade.sh"
try:
    src = open(p).read()
except OSError as e:
    sys.exit("cannot read %s: %s" % (p, e))

old_run = 'if timeout 120 sh -c "$TEST_CMD" >/dev/null 2>&1; then'
new_run = ('echo "=== TEST_CMD: $TEST_CMD" > /logs/verifier/test_output.log\n'
           'echo "=== LANGUAGE: $LANGUAGE  EXERCISE: $EXERCISE" >> /logs/verifier/test_output.log\n'
           'echo "=== EXERCISE_DIR: $EXERCISE_DIR  cwd: $(pwd)" >> /logs/verifier/test_output.log\n'
           'echo "=== files in cwd:" >> /logs/verifier/test_output.log\n'
           'ls -la >> /logs/verifier/test_output.log 2>&1\n'
           'echo "=== test run:" >> /logs/verifier/test_output.log\n'
           'if timeout 120 sh -c "$TEST_CMD" >>/logs/verifier/test_output.log 2>&1; then')
old_cp = ('  if [ -f "$src" ]; then\n'
          '    cp "$src" "$dst"\n'
          '  fi')
new_cp = ('  if [ -f "$src" ]; then\n'
          '    cp "$src" "$dst"\n'
          '    echo "COPIED  $src -> $dst ($(wc -c < "$src") bytes)" >> /logs/verifier/copy.log\n'
          '    mkdir -p /logs/verifier/solution\n'
          '    cp "$src" "/logs/verifier/solution/$(basename "$f")" 2>/dev/null || true\n'
          '  else\n'
          '    echo "MISSING $src (agent did not write it; tests grade the STUB)" >> /logs/verifier/copy.log\n'
          '  fi')

if old_run not in src or old_cp not in src:
    sys.exit("grade.sh does not match the expected shape; refusing to patch blind")

src = src.replace(old_run, new_run, 1).replace(old_cp, new_cp, 1)
src = src.replace("mkdir -p /logs/verifier",
                  "mkdir -p /logs/verifier\n: > /logs/verifier/copy.log", 1)
open(p, "w").write(src)
print("eval-containers: grade.sh patched (test output + copy log preserved)")
PY
  # A syntax error here would make every task score 0.0 for a NEW reason, so
  # revert rather than grade with a broken script.
  if ! bash -n /grade.sh 2>/dev/null; then
    echo "eval-containers: patched grade.sh failed syntax check -- reverting" >&2
    cp -a /grade.sh.orig /grade.sh 2>/dev/null || true
  fi
fi

# --- LOCAL WORKAROUND: disable codex's live web search ------------------------
# NOT FOR UPSTREAM. Tracked as an exgentic issue; delete when the image stops
# hardcoding this.
#
# The gaia--codex image's /run.sh passes `-c 'web_search="live"'` to
# `codex exec`, enabling the native Responses web_search tool. There is no
# search backend reachable from the cluster, so every task that tries to search
# errors instead of falling back to offline reasoning.
#
# It cannot be turned off from the outside: /usr/local/bin/run-agent invokes the
# agent under `env -i` with a strict allow-list, so no env var survives into the
# codex process. The setting exists only in /run.sh.
#
# DELETE the flag rather than setting web_search="off"/false. Deletion lands on
# codex's own documented default -- `--search` is opt-in ("Enable live web
# search", codex-cli 0.120.0) -- whereas the disable enum is NOT verifiable:
# `codex exec -c web_search=bogus --help` exits 0 because --help short-circuits
# before config validation, so a wrong value would fail at task time.
#
# Separate lever from EVAL_GAIA_SUBSET=no-search, which is task SELECTION. Do
# both: the subset stops us grading tasks that need a search engine, this stops
# codex reaching for one on the tasks that remain.
if [[ "${EVAL_DISABLE_WEB_SEARCH:-1}" == "1" && -f /run.sh ]]; then
  if grep -q "web_search=" /run.sh; then
    cp -a /run.sh /run.sh.orig 2>/dev/null || true
    before=$(grep -c "web_search=" /run.sh || true)
    # Drop just the `-c 'web_search="live"'` argument, leaving the rest of the
    # codex invocation (including its line continuations) intact.
    sed -i "s/[[:space:]]*-c[[:space:]]*'web_search=\"live\"'//g" /run.sh || true
    after=$(grep -c "web_search=" /run.sh || true)
    if [[ "$after" -lt "$before" ]] && bash -n /run.sh 2>/dev/null; then
      echo "eval-containers: codex web_search disabled (removed $((before-after)) flag(s) from /run.sh)"
    else
      echo "eval-containers: WARNING web_search patch failed syntax check or matched nothing; reverting" >&2
      cp -a /run.sh.orig /run.sh 2>/dev/null || true
    fi
  fi
fi

# --- run the eval ------------------------------------------------------------
# image ENTRYPOINT stages /app for EVAL_TASK_ID, then execs the pipeline.
rc=0
"${EVAL_CONTAINERS_ENTRYPOINT:-/entrypoint.sh}" \
  "${EVAL_CONTAINERS_RUN:-/usr/local/bin/run}" || rc=$?

# --- hand results back to llm-d-benchmark's collector ------------------------
output_dir="${EVAL_OUTPUT_DIR:-/output}"
if [[ -d "$output_dir" ]]; then cp -a "$output_dir/." "$results_dir/"; fi

# Report what traces were captured, so a run that silently lost them is visible
# in the harness log instead of only discoverable by digging afterwards.
if [[ -s "$results_dir/traces.jsonl" ]]; then
  span_lines=$(wc -l < "$results_dir/traces.jsonl" | tr -d ' ')
  echo "eval-containers: captured traces.jsonl ($span_lines OTLP records)"
else
  echo "eval-containers: WARNING no traces captured for task $EVAL_TASK_ID" >&2
fi

# Collect the verifier's own logs. The eval writes grading output to
# /logs/verifier, which is OUTSIDE /output and so was otherwise discarded with
# the pod. Without it a `reward: 0.0` is unattributable: there is no way to tell
# a genuinely wrong answer from a build/test-harness mismatch (missing file,
# wrong path, compile error) after the fact -- the agent's own stdout only says
# what it *believed* it did.
if [[ -d /logs ]]; then
  mkdir -p "$results_dir/logs"
  cp -a /logs/. "$results_dir/logs/" 2>/dev/null || true
fi

printf 'harness_name: eval-containers\nharness_rc: %s\ntask_id: %s\nmodel: %s\n' \
  "$rc" "$EVAL_TASK_ID" "$EVAL_MODEL" > "$results_dir/run_metadata.yaml"

exit "$rc"
