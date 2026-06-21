# eval-containers harness

Run [eval-containers](https://github.com/Exgentic/eval-containers) agentic
benchmarks as an llm-d-benchmark harness, so a coding/agent workload exercises a
deployed llm-d stack the same way a load generator does -- except each request
is a real multi-turn agent solving a real task against the served model.

Two benchmarks ship as examples:

| Benchmark | Tasks | What the agent does | Internet | Grading |
|-----------|------:|---------------------|----------|---------|
| `aider-polyglot` | 225 | Edit Exercism exercises across 6 languages (cpp/go/java/javascript/python/rust) until the test suite passes | no (fully offline) | per-language unit tests |
| `gaia` | 165 | Answer general-assistant questions using web + tools | yes (pod needs egress) | exact-match |

## How it plugs in

The integration is **additive -- no framework code changes**. It uses three
mechanisms llm-d-benchmark already provides:

1. **Harness pod image.** `images.benchmark` in the scenario sets the image the
   harness pod runs. We point it at a published
   `evals/<benchmark>--<agent>-standalone` bundle -- a single container with the
   agent, an in-pod model gateway, the grader, and OpenTelemetry, the whole
   eval-containers pipeline.
2. **Harness script via ConfigMap.**
   [`workload/harnesses/eval-containers-llm-d-benchmark.sh`](../workload/harnesses/eval-containers-llm-d-benchmark.sh)
   ships in the `llmdbench-harness-scripts` ConfigMap (mounted at
   `/workspace/harnesses`), so it runs inside the eval image even though it is
   not baked there. The scenario sets `harness.entrypoint` to this script so it
   runs in place of the default load-generator entrypoint.
3. **Per-pod task selection.** `-j N` fans the run out across `N` pods. Each pod
   gets its own results dir suffixed `_<idx>` (1..N); the script derives its
   0-based eval-containers `EVAL_TASK_ID` from that suffix, so pod *i* runs task
   *i-1*. Run a 225-task benchmark with `-j 225` (or in waves with
   `EVAL_TASK_OFFSET`).

The script points the eval's in-pod gateway at the deployed model by exporting
`OPENAI_API_BASE=<llm-d endpoint>` and `EVAL_MODEL=openai/<model>` (and leaving
`ANTHROPIC_BASE_URL` unset, which selects the eval's single-image pipeline). The
agent's LLM calls therefore land on the llm-d-served model; results (reward +
agent logs + OTel traces) are copied back to the harness results dir.

Serving-performance numbers (throughput, TTFT, ITL) come from llm-d-benchmark's
standard Prometheus metrics collection, which runs regardless of harness -- this
harness's contribution is realistic, multi-turn, tool-using *load* against the
stack. The copied task artifacts (reward) are a capability signal, not a serving
metric, so the harness does not emit a Benchmark Report; a `benchmark_report`
writer that surfaces reward alongside the serving metrics is possible follow-up.

## Quick start

The example scenarios deploy a tiny CPU model (`llm-d-inference-sim`,
`facebook/opt-125m`) so the wiring runs anywhere without GPUs. That model is
**only a smoke test** -- it cannot actually solve agentic tasks. For meaningful
scores, swap the `model.*` / `images.vllm` blocks for a capable GPU-served model
(see `config/scenarios/examples/gpu.yaml`).

```bash
# aider-polyglot: deploy the model, run the first 8 tasks (one per pod), tear down
llmdbenchmark --spec examples/eval-containers-aider-polyglot standup  -p "$NS"
llmdbenchmark --spec examples/eval-containers-aider-polyglot run      -p "$NS" -j 8
llmdbenchmark --spec examples/eval-containers-aider-polyglot teardown -p "$NS"

# gaia (harness pods need internet egress) -- e.g. all 165 tasks
llmdbenchmark --spec examples/eval-containers-gaia standup -p "$NS"
llmdbenchmark --spec examples/eval-containers-gaia run     -p "$NS" -j 165
```

`-j N` fans the benchmark across `N` pods (one task each); the scenario's
`harness:` block already selects `eval-containers` + the profile, so `-l`/`-w`
are optional. Run a bigger slice in waves with `EVAL_TASK_OFFSET` (wave *k*:
`EVAL_TASK_OFFSET = k*N`, `-j N`).

## Adding another benchmark

eval-containers publishes ~250 benchmarks. To run a different one, point
`images.benchmark.repository` at its
`evals/<benchmark>--<agent>-standalone` image and add a matching
`workload/profiles/eval-containers/<benchmark>.yaml.in`; nothing else changes.

## Caveats

- **Model capability.** The sim base is a connectivity smoke test. Agentic
  benchmarks need a capable instruct model to score above zero.
- **GAIA egress + dataset gating.** GAIA's agent browses the web, so the harness
  pod needs internet egress (allow it in any NetworkPolicy). If the deployment
  gates the dataset behind a HuggingFace token, enable `huggingface.*` so the
  pod template injects `HF_TOKEN`.
- **Image tags.** The examples pin `v0.1.0`. Bump the `images.benchmark.tag` to
  pick up newer eval-containers releases.
