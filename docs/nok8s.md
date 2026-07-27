# No-Kubernetes (`nok8s`) deployment

## Concept

The `nok8s` deployment method runs the llm-d routing stack — **vLLM + EPP
(router) + Envoy** — as plain `docker`/`podman` containers on a single host,
with **no Kubernetes cluster**. The benchmark harness (`llmdbenchmark run`)
also runs as a **local container** against the Envoy front door, so the entire
standup → run → teardown lifecycle is cluster-free.

```
client ──▶ Envoy :8081 ──ext_proc──▶ EPP :9002 ──▶ picks a worker
              │                        (reads endpoints.yaml, file-discovery)
              └──────────────────────▶ vLLM :8000  (OpenAI-compatible API)
```

Instead of watching a Kubernetes `InferencePool`, the EPP reads its worker
inventory from a YAML file via the file-discovery plugin. This is the harness
equivalent of the upstream
[llm-d no-Kubernetes guide](https://github.com/llm-d/llm-d/tree/main/guides/no-kubernetes-deployment).

Use it for HPC/Slurm nodes, bare-metal boxes, or a single GPU workstation
where standing up Kubernetes is not worth it.

## Prerequisites

`llmdbenchmark standup` validates these automatically in step 00; a missing or
broken container runtime is fatal, the rest are warnings.

| Requirement | Notes |
|-------------|-------|
| Linux host + NVIDIA GPU(s) | Images/flags are NVIDIA + vLLM specific |
| NVIDIA driver | `nvidia-smi` must work |
| Container runtime | **docker** or **podman** (`nok8s.runtime`) |
| NVIDIA Container Toolkit | docker: `nvidia-ctk runtime configure --runtime=docker`; podman: `nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml` |
| Hugging Face token | `export HUGGING_FACE_HUB_TOKEN=hf_...` (only for gated models) |
| Free host ports | 8000 (vLLM), 8081 (Envoy), 9002/9003/9090 (EPP), 19000 (Envoy admin) |
| Outbound network | to pull images (docker.io, ghcr.io) and model weights (Hugging Face) |
| `llmdbenchmark` CLI | `./install.sh` (Python 3.11+) |

**Not required:** Kubernetes, `kubectl`/`oc`, `helm`/`helmfile`, Gateway CRDs,
PVCs, or any cluster access.

Verify GPU-in-container before you start:
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Usage

```bash
export HUGGING_FACE_HUB_TOKEN=hf_...     # for gated models

# Bring up vLLM + EPP + Envoy as local containers (step 00 runs the preflight).
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . standup --methods nok8s

# Benchmark it — the harness runs as a local container against http://localhost:8081.
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . run

# Remove the containers.
llmdbenchmark --spec config/specification/guides/nok8s.yaml.j2 --base-dir . teardown --methods nok8s
```

`--methods nok8s` is optional when the scenario sets `nok8s.enabled: true`
(the method is auto-detected), but harmless to pass explicitly.

Send your own requests to the Envoy front door:
```bash
curl -s http://localhost:8081/v1/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","prompt":"Hello","max_tokens":32}'
```

## Configuration

Selected by `nok8s.enabled: true` in a scenario (mutually exclusive with
`modelservice`/`standalone`/`fma`/`kustomize`). See
[`config/scenarios/guides/nok8s.yaml`](../config/scenarios/guides/nok8s.yaml) for a complete
single-GPU example. Key fields (full defaults in
`config/templates/values/defaults.yaml`):

| Key | Meaning |
|-----|---------|
| `nok8s.runtime` | `docker` or `podman` (GPU flag switches to CDI for podman) |
| `nok8s.hfTokenEnv` | Host env var passed to vLLM for HF auth |
| `nok8s.workspaceHostDir` | Host dir where EPP/Envoy configs are staged + bind-mounted |
| `nok8s.vllm.{image,tag,hostPort,tensorParallel,accelerator,gpus,deviceArgs,shmSize,replicas,extraArgs}` | vLLM worker(s); worker *i* is published on `hostPort + i` (see Accelerators for `accelerator`/`deviceArgs`) |
| `nok8s.epp.{image,tag,grpcPort,grpcHealthPort,metricsPort}` | Endpoint Picker |
| `nok8s.envoy.{image,tag,listenPort}` | Envoy front door (the run target) |
| `model.{name,huggingfaceId}` | Set both; standup uses `name`, run reads `huggingfaceId` |

Sizing (fp16, single GPU): ~16 GB → 7–8B · ~24 GB → 8B · ~40 GB → 14B ·
~80 GB → 32B. For bigger models use `nok8s.vllm.extraArgs`
(e.g. `["--max-model-len","16384","--gpu-memory-utilization","0.95"]`) or an
FP8 checkpoint.

## Accelerators

The router (EPP + Envoy) and the benchmark harness are accelerator-agnostic;
only the **vLLM worker** is accelerator-specific. Select the accelerator with
`nok8s.vllm.accelerator` and set `nok8s.vllm.image` to the matching vLLM
backend. Only **NVIDIA is validated end-to-end**; the others use each backend's
documented device flags.

| `accelerator` | Device flags emitted | vLLM image (example) |
|---------------|----------------------|----------------------|
| `nvidia` (default) | `--gpus all` (docker) / `--device nvidia.com/gpu=all` (podman) | `vllm/vllm-openai` |
| `amd` | `--device /dev/kfd --device /dev/dri --group-add video` | `rocm/vllm` |
| `intel` | `--device /dev/dri` | Intel vLLM XPU image |
| `gaudi` | `--runtime=habana -e HABANA_VISIBLE_DEVICES=all` | Habana vLLM image |
| `cpu` | *(none)* | vLLM CPU build |
| `spyre` | *(none -- set `deviceArgs`)* | IBM vLLM-Spyre image |

For anything not covered by a preset — including **IBM Spyre / AIU** — use the
raw escape hatch `nok8s.vllm.deviceArgs`, which overrides the preset entirely:

```yaml
nok8s:
  vllm:
    accelerator: spyre
    image: <ibm-vllm-spyre-image>
    deviceArgs: ["--device", "/dev/vfio/vfio", "--device", "/dev/vfio/<grp>"]
    extraArgs: ["--..."]   # any Spyre-specific vLLM flags
```

Step 00 probes the accelerator when it can (`nvidia-smi`/`rocm-smi`/`xpu-smi`/
`hl-smi`); `cpu`/`spyre`/custom are not probed (a note is logged) — ensure the
device and image match yourself.

## How it maps to the pipeline

| Phase | nok8s behaviour |
|-------|-----------------|
| standup step 00 | Preflight: runtime / GPU / ports / token (replaces helm/kubectl checks) |
| standup steps 02–05 | Skipped (no namespace, PVC, model-download Job) |
| standup step 06 | `step_06_nok8s_deploy` launches the containers, waits for `/v1/models`, records `http://localhost:<listenPort>` |
| run step 03 | Endpoint resolves to the local Envoy URL (no cluster query) |
| run step 07 | `step_07_deploy_harness_local` runs the harness image locally with `--network host`; results land in `workspace/results/` via a bind-mount |
| teardown step 06 | `step_06_nok8s_teardown` removes the containers |

## Multiple GPUs

Two modes, driven by `tensorParallel` and `replicas`:

**One model sharded across GPUs (tensor parallelism)** — serve a large model:
```yaml
nok8s:
  vllm:
    tensorParallel: 4      # shard one model across 4 GPUs
    replicas: 1
    shmSize: "40g"         # bump for NCCL/RCCL with TP > 1
```
→ `--gpus all --tensor-parallel-size=4`.

**Multiple independent workers (throughput / router load-balancing)** — set
`replicas: N`. Each worker is published on `hostPort + i`, added to the EPP
endpoints file (so the router load-balances / prefix-routes across them), and
**pinned to its own slice of `tensorParallel` GPU indices** (replica *i* →
devices `i*TP .. i*TP+TP-1`) via the accelerator's visible-devices env
(`CUDA_VISIBLE_DEVICES` / `HIP_VISIBLE_DEVICES` / `ZE_AFFINITY_MASK`) so workers
don't contend for the same GPUs.

**Total GPUs used = `replicas × tensorParallel`.** Examples on an 8-GPU host:
| Goal | `replicas` | `tensorParallel` |
|------|-----------|------------------|
| One big model sharded 8-way | 1 | 8 |
| 8 workers, 1 GPU each (max throughput) | 8 | 1 |
| 2 workers, each sharded over 4 | 2 | 4 |

Step 00 warns if `replicas × tensorParallel` exceeds the detected GPU count
(NVIDIA). Per-replica pinning uses index-based env vars for nvidia/amd/intel;
for gaudi/spyre or custom wiring, set `nok8s.vllm.deviceArgs` (which disables
auto-pinning and puts you in control).

## Troubleshooting

- **Preflight fails on runtime** — install docker/podman or set `nok8s.runtime`.
- **vLLM container exits at load** — usually a too-large model for VRAM or a bad
  HF token; check `docker logs vllm-0`. Lower the model size or add
  `--max-model-len`/`--gpu-memory-utilization` via `nok8s.vllm.extraArgs`.
- **Envoy 503** — a worker isn't up; confirm `curl http://localhost:8000/v1/models`.
- **podman + GPU** — ensure the CDI spec exists: `nvidia-ctk cdi list` should show
  `nvidia.com/gpu=all`.
- **Ports busy** — a stale run; `teardown --methods nok8s`, or change the ports.
