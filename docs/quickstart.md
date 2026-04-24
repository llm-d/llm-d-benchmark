# Quickstart

This guide walks you through running your **first llm-d-benchmark deployment on a local [Kind](https://kind.sigs.k8s.io/) cluster** - no GPU required. By the end you will have stood up a simulated inference deployment, run a sanity benchmark workload against it, and torn everything down cleanly.

This is the same scenario our CI runs on every PR (see [`ci-pr-benchmark.yaml`](../.github/workflows/ci-pr-benchmark.yaml)), so if the walkthrough works here it will work the same way in CI.

> ### When to use Kind
>
> **Use Kind only if you don't have access to a real cluster with GPU / accelerator resources, or if you're doing local development on a laptop.**
>
> Kind is a local-Docker-in-Docker Kubernetes distribution. It is ideal for:
>
> - **First-time walkthroughs of the framework** - you can exercise the full `standup -> smoketest -> run -> teardown` lifecycle without any cloud account, cluster access, or GPU hardware.
> - **Iterating on framework code** - testing your changes to steps, templates, or scenarios locally in a fast feedback loop.
> - **Reproducing CI failures** - the PR-benchmark workflow uses this exact `cicd/kind-sim` scenario on a Kind cluster, so a local repro is one `./util/test-scenarios.sh` invocation away.
>
> Kind is **not** a benchmarking target. It runs a simulated inference engine (`llm-d-inference-sim`) on CPU, so any latency, throughput, or GPU-utilization numbers you collect here are meaningless as performance data. When you have access to a cluster with real accelerators, switch to one of the GPU-backed scenarios under [`config/specification/examples/gpu.yaml.j2`](../config/specification/examples/gpu.yaml.j2) or [`config/specification/guides/`](../config/specification/guides/) and skip steps 1 and 2 of this guide - jump straight to [step 3 (Install llmdbenchmark)](#3-install-llmdbenchmark) and use your existing kubeconfig.

## Table of Contents

- [What you will build](#what-you-will-build)
- [Prerequisites](#prerequisites)
- [1. Install Kind locally](#1-install-kind-locally)
- [2. Create the Kind cluster](#2-create-the-kind-cluster)
- [3. Install llmdbenchmark](#3-install-llmdbenchmark)
- [4. First deployment: standup + smoketest + run (modelservice)](#4-first-deployment-standup--smoketest--run-modelservice)
- [5. Alternate path: standalone deployment](#5-alternate-path-standalone-deployment)
- [6. Tear down](#6-tear-down)
- [Troubleshooting](#troubleshooting)
- [Next steps](#next-steps)

## What you will build

| Item | Value |
|---|---|
| Cluster | Kind (local Docker-in-Docker, CPU-only) |
| Scenario | [`cicd/kind-sim`](../config/scenarios/cicd/kind-sim.yaml) |
| Model | `facebook/opt-125m` (small - chosen so the quickstart works on a laptop) |
| Inference engine | [`llm-d-inference-sim`](https://github.com/llm-d/llm-d-inference-sim) - fake inference, no GPU |
| Deploy methods | `modelservice` (default) or `standalone` |
| Harness | `inference-perf` with the `sanity_random.yaml` workload profile |

Because the inference engine is simulated, the entire stack runs on a CPU-only machine in a single Kind node. Nothing in this walkthrough requires a GPU, a cluster operator, or a cloud account.

## Prerequisites

You need these installed before starting:

| Tool | Minimum | Check |
|---|---|---|
| Docker or Podman | any recent version | `docker info` or `podman info` |
| Python | 3.11+ | `python3 --version` |
| `git` | any | `git --version` |
| Container runtime resources | **4 CPUs / 8 GiB RAM** | `docker info \| grep -E "CPUs\|Total Memory"` |

> **Resource note:** The `cicd/kind-sim` scenario deploys ~7 pods on a single Kind node. With the default 2 CPUs that Docker Desktop, Colima, and Podman ship with, the harness pod (and sometimes the gateway) cannot schedule due to `Insufficient cpu`. Bump your container runtime to **4 CPUs** before creating the Kind cluster. See [Troubleshooting](#pods-stuck-in-pending-during-standup-or-run) if you hit this.

Everything else - `kubectl`, `helm`, `helmfile`, `kind`, `skopeo`, `crane`, `helm-diff`, `jq`, `yq`, `kustomize` - will be installed for you by `./install.sh` in [step 3](#3-install-llmdbenchmark), with one exception: `kind` itself, which we install first below because we want the cluster up before the installer runs.

## 1. Install Kind locally

Kind runs Kubernetes clusters inside Docker containers. Pick the line for your OS:

### macOS (Homebrew)

```bash
brew install kind
```

### Linux (amd64)

```bash
# v0.31.0 is the version CI uses. Pin to it for parity.
curl -Lo ./kind "https://kind.sigs.k8s.io/dl/v0.31.0/kind-linux-amd64"
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind
```

### Linux (arm64)

```bash
curl -Lo ./kind "https://kind.sigs.k8s.io/dl/v0.31.0/kind-linux-arm64"
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind
```

### Verify

```bash
kind version
# Expected: kind v0.31.0 ...
```

If you prefer a different installation path or version manager, see the [upstream Kind install docs](https://kind.sigs.k8s.io/docs/user/quick-start/#installation). Any version v0.20+ should work; v0.31.0 is what CI exercises.

## 2. Create the Kind cluster

Create a single-node cluster. The default Kind configuration is enough - we do **not** need any special port mappings, extra mounts, or registry config for `cicd/kind-sim`.

```bash
kind create cluster --name llmd-quickstart
```

The first run pulls the Kind node image, which can take a while depending on your network. When it finishes, your `kubectl` context is automatically pointed at the new cluster. Verify:

```bash
kubectl cluster-info --context kind-llmd-quickstart
kubectl get nodes
# Expected: one node in Ready state
```

That's all the cluster prep you need. The `cicd/kind-sim` scenario uses `affinity.nodeSelector: kubernetes.io/os: linux`, and Kubernetes sets that label automatically on every node via the kubelet (it is one of the [well-known labels](https://kubernetes.io/docs/reference/labels-annotations-taints/#kubernetes-io-os)), so no manual labeling step is required.

## 3. Install llmdbenchmark

Clone the repository and run the installer. It creates `.venv/`, installs the `llmdbenchmark` and `planner` (from [llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner)) Python packages, and provisions every system tool the framework calls out to.

```bash
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
./install.sh
source .venv/bin/activate
```

We intentionally **do not** pass `-y` to `install.sh`. The `-y` flag forces the installer to use your system Python instead of creating a virtualenv, which is appropriate on CI runners (they are already isolated containers) but wrong for local development - it would pollute your system site-packages and skip the `.venv/` that `source .venv/bin/activate` on the next line expects. Always run `./install.sh` without flags on your laptop.

Verify the CLI is on PATH:

```bash
llmdbenchmark --help
```

You should see the `llmdbenchmark` help banner with `plan`, `standup`, `smoketest`, `run`, `teardown`, and `experiment` subcommands.

> **Tip:** The installer caches its own "already checked" state in `~/.llmdbench_dependencies_checked`. Subsequent `./install.sh` runs skip dependencies that have already been verified.

## 4. First deployment: standup + smoketest + run (modelservice)

Now we run the full four-phase lifecycle against the Kind cluster we created in [step 2](#2-create-the-kind-cluster). `modelservice` is the default deploy method - no `-t` flag needed.

Pick a namespace for your run. Anything unique is fine:

```bash
export NS=llmd-quickstart
```

### 4a. Standup

`standup` renders the scenario templates, installs the llm-d charts, creates the model PVC, downloads the model, and deploys the prefill + decode pods.

```bash
llmdbenchmark --spec cicd/kind-sim standup -p "$NS" --skip-smoketest
```

What's happening under the hood:

1. The `cicd/kind-sim` specification is rendered into a self-contained `plan/` directory under `$LLMDBENCH_WORKSPACE` (defaults to `/tmp/<user>-<timestamp>`).
2. The `modelservice` Helm chart is deployed into `$NS` with CPU-only image overrides.
3. A PVC is provisioned and `facebook/opt-125m` is downloaded into it from HuggingFace.
4. Prefill, decode, and gateway pods are rolled out and wait for Ready.

The first run is dominated by the model download and image pulls. Subsequent runs (different namespace) reuse pulled images and are noticeably faster.

Progress banners at the start of each step make it easy to follow along. If a step fails, the banner at the top shows which phase and which step number hit the error, and the pod logs are printed inline.

### 4b. Smoketest

Once standup succeeds, run the smoketest to verify the inference endpoint actually answers:

```bash
llmdbenchmark --spec cicd/kind-sim smoketest -p "$NS"
```

This sends a handful of real requests through the gateway, validates the responses, and exits 0 on success.

### 4c. Run the benchmark

Now run the `inference-perf` harness with the `sanity_random.yaml` workload. This is the smallest benchmark profile we ship - perfect for a first run.

```bash
llmdbenchmark --spec cicd/kind-sim run -p "$NS" \
    -l inference-perf \
    -w sanity_random.yaml
```

What to expect:

- A harness pod is launched in `$NS`.
- It fires a burst of requests against the gateway.
- Per-request metrics are collected into a results directory printed at the end of the run.
- The analysis phase generates summary CSVs and plots in that same directory.

The results directory path is printed in the final log line - something like `/tmp/<user>-<timestamp>/<phase>/<stack>/results/`. You can open the plots with any image viewer or the CSVs with any spreadsheet.

## 5. Alternate path: standalone deployment

The `standalone` method skips the llm-d-modelservice chart entirely and deploys a single vLLM pod directly. It's simpler, has fewer moving parts, and is a good second step after the modelservice path succeeds.

Use a different namespace so you don't clash with the modelservice run:

```bash
export NS_SA=llmd-quickstart-sa

llmdbenchmark --spec cicd/kind-sim standup   -p "$NS_SA" -t standalone --skip-smoketest
llmdbenchmark --spec cicd/kind-sim smoketest -p "$NS_SA" -t standalone
llmdbenchmark --spec cicd/kind-sim run       -p "$NS_SA" -t standalone \
    -l inference-perf -w sanity_random.yaml
```

The `-t standalone` flag is the only difference from [step 4](#4-first-deployment-standup--smoketest--run-modelservice). Every other argument - spec, namespace, harness, workload - is identical.

## 6. Tear down

Clean up the deployment(s) but leave the Kind cluster itself running:

```bash
# Tear down the modelservice namespace
llmdbenchmark --spec cicd/kind-sim teardown -p "$NS"

# Tear down the standalone namespace (if you ran step 5)
llmdbenchmark --spec cicd/kind-sim teardown -p "$NS_SA" -t standalone
```

Or, if you're done with the cluster entirely, delete it wholesale:

```bash
kind delete cluster --name llmd-quickstart
```

## Troubleshooting

### `kind create cluster` hangs or fails

- **Docker not running**: start Docker Desktop / Colima / Podman and retry.
- **Low disk space**: Kind needs free space in `/tmp` and `/var/lib/docker`. `docker system prune -a` frees cache space.
- **Previous cluster still around**: `kind get clusters` then `kind delete cluster --name <name>`.

### Pods stuck in `Pending` during standup or run

- **Insufficient CPU or memory on the Kind node**: this is the most common issue on laptops. Run `kubectl describe pod -n "$NS" <pod>` and look for events like:

  ```
  Warning  FailedScheduling  0/1 nodes are available: 1 Insufficient cpu, 1 Insufficient memory.
  ```

  The `cicd/kind-sim` scenario needs roughly **2.5 CPU** across all pods (decode, prefill, EPP, gateway, harness). If your container runtime (Docker Desktop, Colima, Podman) defaults to 2 CPUs, the harness pod won't fit alongside everything else.

  **Check your current allocation:**

  ```bash
  # Docker Desktop / Colima / Podman - any of these will work:
  docker info 2>/dev/null | grep -E "CPUs|Total Memory"
  podman info 2>/dev/null | grep -E "cpus|memTotal"
  colima status 2>/dev/null

  # Or check what Kubernetes actually sees:
  kubectl describe node | grep -A6 "Allocated resources"
  ```

  **Fix - increase CPUs to at least 4 (8 GiB RAM recommended):**

  ```bash
  # Docker Desktop: Settings > Resources > CPUs: 4, Memory: 8 GiB
  # (no CLI option - must be done through the GUI)

  # Colima
  colima stop && colima start --cpu 4 --memory 8

  # Podman
  podman machine stop && podman machine set --cpus 4 --memory 8192 && podman machine start
  ```

  After changing resources, **recreate the Kind cluster** (the kubelet captures allocatable resources at node boot):

  ```bash
  kind delete cluster --name llmd-quickstart
  kind create cluster --name llmd-quickstart
  ```

  Then re-run standup from scratch.

- **PVC stuck**: `kubectl get pvc -n "$NS"` - the `standard` Kind storage class should provision immediately. If it does not, you're probably out of disk; see above.
- **Image pull backoff**: check `kubectl describe pod -n "$NS" <pod>` for the failing image and make sure your machine has network access to `ghcr.io`.
- **Node selector mismatch**: if `kubectl describe pod -n "$NS" <pod>` shows `0/1 nodes are available: 1 node(s) didn't match Pod's node affinity/selector`, print the node's labels with `kubectl get node -o jsonpath='{.items[0].metadata.labels}' | jq` and cross-check against the scenario's `affinity.nodeSelector` in `config/scenarios/cicd/kind-sim.yaml`. On a standard Kind cluster this should always match because `kubernetes.io/os=linux` is a well-known label the kubelet sets automatically.

### `llmdbenchmark: command not found`

You probably exited the shell between steps. Re-activate the venv:

```bash
cd llm-d-benchmark
source .venv/bin/activate
```

### `helmfile: command not found` or similar tool-missing errors

`./install.sh` installs these for you. If you skipped it or ran from outside the repo, re-run:

```bash
./install.sh
```

(no `-y` - we want the `.venv/` path, not system Python)

### Standup reports "Model download failed"

The `facebook/opt-125m` model is public and small. If the download fails, you most likely have:

- **No network access from inside Kind pods** (corporate proxy, air-gapped laptop): run `kubectl logs -n "$NS" job/download-model --tail=50` to see the actual error.
- **HuggingFace rate limiting**: retry after a short wait, or set a `HUGGING_FACE_HUB_TOKEN` via `-v HUGGING_FACE_HUB_TOKEN=<token>`.

### Run phase hangs on `waiting for harness pod` or reports `No pods deployed`

- `kubectl get pods -n "$NS"` - check if the harness pod is `Pending`. If `kubectl describe pod -n "$NS" <harness-pod>` shows `Insufficient cpu` or `Insufficient memory`, see [Pods stuck in Pending](#pods-stuck-in-pending-during-standup-or-run) above.
- If a previous run failed and left a stale harness pod, clean it up before retrying:

  ```bash
  kubectl delete pod -n "$NS" -l app=llmdbench-harness-launcher --ignore-not-found
  ```

- If you edited `harness.resources` in your scenario to reduce requests, you must re-run `plan` before `run` (no standup needed - the cluster infra is unchanged):

  ```bash
  llmdbenchmark --spec cicd/kind-sim plan -p "$NS"
  llmdbenchmark --spec cicd/kind-sim run  -p "$NS"
  ```

### Anything else

Run with `LLMDBENCH_LOG_LEVEL=DEBUG` for verbose output:

```bash
LLMDBENCH_LOG_LEVEL=DEBUG llmdbenchmark --spec cicd/kind-sim standup -p "$NS"
```

The workspace directory printed at the top of every run contains all rendered templates, step-by-step logs, and pod manifests. That's usually enough to pinpoint any failure without needing extra flags.

## Next steps

You just ran the same lifecycle CI exercises every PR. From here, natural next steps are:

- **Try a real GPU scenario**: see [`config/specification/examples/gpu.yaml.j2`](../config/specification/examples/gpu.yaml.j2) and run it against a cluster that has GPU nodes.
- **Explore the well-lit paths**: [`config/specification/guides/`](../config/specification/guides/) has scenarios for `inference-scheduling`, `inference-scheduling-wva`, `multi-model-wva`, `pd-disaggregation`, `precise-prefix-cache-aware`, `tiered-prefix-cache`, and `wide-ep-lws` - each worth a read even if you don't run them.
- **Try multi-model with WVA**: [`multi-model-wva`](../config/scenarios/guides/multi-model-wva.yaml) deploys two models behind one gateway with a single shared HTTPRoute and a single WVA controller autoscaling each pool independently. Standup: `llmdbenchmark --spec guides/multi-model-wva standup -p <namespace>`.
- **Write a custom scenario**: see the [Developer Guide, Section 7](developer-guide.md#7-how-to-add-a-new-scenario-well-lit-path) - "How to Add a New Scenario".
- **Add a new benchmark step**: see the [Developer Guide, Section 2](developer-guide.md#2-how-to-add-a-new-step) - "How to Add a New Step".
- **Set up pre-commit** so your first PR passes CI on the first try: see [Local Development Checks in CONTRIBUTING.md](../CONTRIBUTING.md#local-development-checks-pre-commit).

If you hit anything that didn't work for you in this guide, please [open an issue](https://github.com/llm-d/llm-d-benchmark/issues) - that's the fastest way to get the guide improved for the next person.
