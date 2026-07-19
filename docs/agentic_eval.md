# Agentic evaluation with the `eval-containers` harness

The `eval-containers` harness runs agentic benchmarks on the llm-d infrastructure for 
generating and evaluating real agent workloads in real time.


- **Concept & data flow** — below
- **Quick start** — [Quick start](#quick-start)
- **Verified scenarios** — [Verified benchmark × agent combinations](#verified-benchmark-agent-combinations)
- **Write your own** — [Constructing a scenario](#constructing-a-scenario)
- **Model config** — [Model serving](#model-serving)
- **Debugging** — [Reading results](#reading-results) and [Debugging](#debugging)

---

## Concept

```
  llmdbenchmark CLI (your workstation)
        │  renders a scenario into a pipeline of steps
        │  talks to the cluster with kubectl/oc
        ▼
  Cluster (kind, or any Kubernetes / OpenShift)
        │  deploys N "harness pods" running the eval image
        │  each image = agent + in-pod gateway + grader + OTel collector
        ▼
  Each agent's model calls flow:
        agent → in-pod gateway → your model endpoint → the model
        ▼
  Results are written to a PVC, then copied back to a local results dir
```

---

## Quick start

This runs one task from the GAIA benchmark with the `codex` agent, in **run-only** mode against an
existing OpenAI-compatible endpoint (see [Model serving](#model-serving) for the
alternatives).

### Prerequisites

- The `llmdbenchmark` CLI installed (see the top-level [README](../README.md)).
- `kubectl` (or `oc` for OpenShift) pointed at your cluster.
- An OpenAI-compatible model endpoint URL, and a key if it requires auth.
- A `ReadWriteMany` storage class for `-j > 1` (parallel pods share the results PVC).

### Run it

```bash
export OPENAI_API_KEY="<your-key>"        # exported so -g forwards it by name  # pragma: allowlist secret

llmdbenchmark --spec examples/eval-containers-gaia run \
  -U "https://<your-endpoint>" -m "<model-id>" -p <namespace> \
  -g OPENAI_API_KEY -j 1 --base-dir . --analyze --wait-timeout 3600
```

---

## Verified benchmark × agent combinations

"Verified" means the serving path was driven end-to-end and the model's calls
landed and were graded. So far that's only these two, and only with a limited
model set (self-served Qwen on GPU, and a proxy/LiteLLM-style
endpoint). Scores are incidental — they reflect model/benchmark difficulty, not
the pipeline.

| Benchmark | Agent | Model | Run mode |
|---|---|---|---|
| `gaia` | `codex` | Qwen3 (self-served GPU) · proxy endpoint | GPU-served, run-only |
| `aider-polyglot` | `gemini-cli` | Qwen3 (self-served GPU) | GPU-served |

---

## Constructing a scenario

A scenario is a YAML describing one run (model, eval image, storage, harness
wiring); anything unset falls back to `config/templates/values/defaults.yaml`.
Shipped examples: `config/scenarios/examples/eval-containers-{gaia,aider-polyglot}.yaml`
(run-only) plus their `-gpu` variants. The three blocks that make it an
`eval-containers` scenario:

```yaml
    images:
      benchmark:                            # the eval image the harness pod runs
        repository: ghcr.io/exgentic/evals/<benchmark>--<agent>
        tag: latest
    harness:
      name: eval-containers                 # selects this harness
      experimentProfile: gaia.yaml          # per-benchmark profile
      entrypoint: /workspace/harnesses/eval-containers-llm-d-benchmark.sh
      resources: { cpu: "2", memory: 6Gi }
    model:
      name: <model-id>                      # becomes EVAL_MODEL=openai/<name>
      huggingfaceId: <hf-id-or-served-id>
```

Change `images.benchmark` + `harness.experimentProfile` to point at a different
benchmark/agent. The `model`/`decode`/`prefill`/`storage` blocks describe **how
the model is served** (next section).

**Keep scenarios cluster-agnostic.** RWX storage class, service account, and root
privilege belong in a `--cluster-config` file
([openshift-setup.md](openshift-setup.md)), merged over the scenario at run time:
`defaults.yaml → scenario.yaml → --cluster-config → CLI flags`.

---

## Model serving

The agent needs an OpenAI-compatible endpoint — two modes, identical downstream
of the gateway.

### A. Proxy-hosted endpoint (run-only mode)

Point the agent at an **existing** endpoint (e.g. a model proxy/router) — no
model is stood up:

```bash
llmdbenchmark --spec examples/eval-containers-gaia run \
  -U "https://<proxy-endpoint>" -m "<model-id>" \
  -p <ns> -g OPENAI_API_KEY -j 1 --base-dir . --analyze
```

The harness pod needs egress to the endpoint; model access is usually
key-dependent (per-key/per-team allow-lists) — confirm the exact key + model can
infer before a full run (a `401`/`403`/`429` there saves a wasted run).

### B. llm-d-served model on cluster GPUs (standup → run → teardown)

Stand up the model with llm-d on GPUs, then run the eval against that in-cluster
endpoint. `run` does *not* auto-standup.

```bash
llmdbenchmark --spec <gpu-scenario> standup  -p <ns> -t modelservice --base-dir .
llmdbenchmark --spec <gpu-scenario> run      -p <ns> -t modelservice -j 1 --base-dir . --analyze  # NO -U / -g
llmdbenchmark --spec <gpu-scenario> teardown -p <ns> -t modelservice --base-dir .
```

A GPU-served scenario (vs run-only) must, beyond the run-only fields: cap
`model.maxModelLen` to fit the KV cache; select GPU nodes (`affinity.nodeSelector`
+ `decode.acceleratorType`) and **remove any `accelerator.count: 0`** (zero forces
CPU serving); use a real vLLM image; set `decode.vllm.modelCommand: custom` so the
serve command actually emits `--max-model-len` / `--tensor-parallel-size` /
`--gpu-memory-utilization`; provide a model PVC (+ HF token if gated); and scale
`decode.replicas` with `-j`. No `-U`/`-g` — the endpoint is auto-discovered.
See the `-gpu` example scenarios. On OpenShift the images run as **root**, so a
cluster-admin binds an `anyuid` ServiceAccount once per namespace and your
`--cluster-config` references it ([openshift-setup.md](openshift-setup.md)).

---

## Reading results

Per task, `eval-containers-<id>_<n>/` contains:

| File | Tells you |
|---|---|
| `task/result.json` | **The score**: `{"reward":1.0,"passed":true}` (grader output in `task/verifier.log`). |
| `agent/stdout.log` | The agent's work. Empty = it produced nothing. |
| `model/gateway.log` | The in-pod gateway — first place to look on a call failure. |
| `traces.jsonl` | One OTel batch per model call. **0 lines = no calls reached the model.** |

There is **no aggregate score**: each task writes its own `result.json`. A healthy run:
 gateway logs a successful startup, `traces.jsonl` has `llm.call` spans with status `200`, 
 and `stdout.log` is non-empty and free of `API Error`.

### Debugging

| Symptom | Cause → Fix |
|---|---|
| Agent runs but the served model **rejects a tool** (e.g. `unsupported call: web_search`, or a `400` on a tool field) | The served model doesn't support a tool the agent sends. Serve a model/route that accepts it, or use an agent whose tools the model supports. |
| Agent errors on structured output (e.g. gemini-cli `NumericalClassifierStrategy` / "invalid content, retries exhausted") | The served model returns tool-call/JSON in a format the agent's internal router can't parse. Pair the agent with a model whose output format it handles (verified: Qwen-class works with both `codex` and `gemini-cli`). |
| At `-j N`, agent stdout ends mid-work (task cut off) | Too many agents per model replica → per-task timeout. Raise `decode.replicas` (~2–3 tasks/replica). |

See also: [run.md](run.md) (harness/profile mechanics) ·
[openshift-setup.md](openshift-setup.md) (cluster-config + OpenShift prereqs) ·
[analysis.md](analysis.md) (`--analyze` report).
