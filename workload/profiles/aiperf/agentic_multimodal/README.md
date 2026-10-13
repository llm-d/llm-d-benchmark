# Agentic Multimodal Workload (`agentic_multimodal`) for `aiperf`

This directory (`workload/profiles/aiperf/agentic_multimodal/`) provides the dataset preparation script and usage instructions for the [`agentic_multimodal.yaml.in`](../agentic_multimodal.yaml.in) profile, benchmarking `llm-d` multimodal serving with **[`kellyhao/mmshopbench`](https://huggingface.co/datasets/kellyhao/mmshopbench)** (*MMShopBench: A Real-Log Benchmark for Multimodal, Multi-Turn Shopping Agents*) using **NVIDIA `aiperf`** (`--custom-dataset-type multi_turn`).

## 1. Workload Overview

Unlike synthetic single-turn or static shared-prefix multimodal benchmarks, `mmshopbench` replays real e-commerce shopping sessions:
* **288 multi-turn conversations** (`1,301` total turns; `3` to `19` turns per session, mean `4.52` turns/session).
* **400 real product query photos** (`val_pic/*.jpg`) with variable resolutions and aspect ratios, exercising dynamic visual token estimation (`token-producer` in `llm-d`'s EPP router).
* **Later-round image uploads (`28.3%`)**: `287` image turns occur on Round 1, while **`113` image turns (`28.3%`)** occur on **Round 2+**, testing whether `prefix-cache-affinity-filter` (`affinityThreshold: 0.6`, `maxTTFTPenaltyMs: 36000`) preserves session affinity when a user uploads a follow-up photo mid-conversation.

### Why Only `~42 MB` of the `553 MB` Repository is Downloaded
The HuggingFace repository contains four items, but serving latency/throughput benchmarks only need the conversation trace and raw images:

| File / Path | Size | Needed for `aiperf`? | Notes |
| :--- | :--- | :---: | :--- |
| `multi_run_hardcase_merged_num292_regen.json` | `1.77 MB` | **Yes** | Source of 288 multi-turn sessions, user prompts, image paths, and target output token lengths. |
| `val_pic/*.jpg` | `~40 MB` | **Yes** | 400 user query photos encoded as `data:image/jpeg;base64` by `aiperf`. |
| `bm25.pkl` | `87.6 MB` | No | Offline BM25 product search index (skipped). |
| `image_embeddings.npy` | `424.1 MB` | No | Precomputed CLIP embeddings for offline catalog search (skipped; vLLM runs its own ViT encoder). |

> **Note on Image Encoding**: Many `val_pic/*.jpg` files in the upstream HuggingFace repository are WebP-encoded files saved with a `.jpg` extension. `convert_mmshopbench_to_aiperf.py` automatically transcodes them into true RGB `JPEG` (`quality=95`, preserving exact `width × height` pixel dimensions) so `aiperf`'s `PIL.Image` validator (`JPEG`/`PNG`) succeeds.

---

## 2. Step 1: Prepare the Dataset

Run `convert_mmshopbench_to_aiperf.py` inside the benchmark harness pod (or on the host before copying to the PVC/pod):

```bash
python3 workload/profiles/aiperf/agentic_multimodal/convert_mmshopbench_to_aiperf.py \
  --output-dir /requests/agentic_multimodal
```

This creates:
* `/requests/agentic_multimodal/raw/val_pic/*.jpg` (`400` RGB JPEG images)
* `/requests/agentic_multimodal/aiperf_agentic_multimodal.jsonl` (`288` multi-turn sessions)

If you run the script on a local machine and copy the directory into `/workspace/agentic_multimodal` inside a pod via `kubectl cp`, pass `--runtime-dir /workspace/agentic_multimodal` so image paths in the JSONL match the pod mount path:

```bash
python3 workload/profiles/aiperf/agentic_multimodal/convert_mmshopbench_to_aiperf.py \
  --output-dir ./agentic_multimodal \
  --runtime-dir /workspace/agentic_multimodal
```

---

## 3. Step 2: Run the Benchmark

### Option A: Using `llmdbenchmark` with `agentic_multimodal.yaml.in`

Stage `/requests/agentic_multimodal` on the harness PVC and run using the `aiperf` harness profile [`agentic_multimodal.yaml.in`](../agentic_multimodal.yaml.in):

```bash
llmdbenchmark --spec <your-multimodal-spec> \
  --harness aiperf \
  --workload agentic_multimodal.yaml \
  --dataset /requests/agentic_multimodal/aiperf_agentic_multimodal.jsonl
```

### Option B: Direct `aiperf profile` Execution (Concurrency Sweep)

Inside a `ghcr.io/llm-d/llm-d-benchmark` client pod where `/workspace/agentic_multimodal` is staged:

```bash
aiperf profile \
  --model "Qwen/Qwen3-VL-32B-Instruct" \
  --url "http://aggregation-epp.llm-d-multimodal-aggregation.svc.cluster.local:80" \
  --endpoint-type chat \
  --streaming \
  --input-file /workspace/agentic_multimodal/aiperf_agentic_multimodal.jsonl \
  --custom-dataset-type multi_turn \
  --conversation-num 576 \
  --conversation-turn-delay-mean 2000 \
  --concurrency 64 \
  --extra-inputs '{"ignore_eos": true}' \
  --random-seed 42 \
  --ui-type none \
  --output-artifact-dir /workspace/results/llmd_c64
```

* **Scaling `--conversation-num` at High Concurrency**: `aiperf` requires `concurrency <= conversation-num`. The base dataset has `288` unique sessions; setting `--conversation-num 576` (for `concurrency <= 256`) or `1152` (for `concurrency = 512`) wraps around the dataset so at least `2×` waves of sessions execute concurrently.
* **Resetting vLLM Caches Between Sweep Points**: When sweeping concurrency levels (`8, 16, 32, 64, 128, 256, 512`), set `VLLM_SERVER_DEV_MODE=1` on the vLLM deployment and call `POST /reset_prefix_cache`, `POST /reset_mm_cache`, and `POST /reset_encoder_cache` on each vLLM pod (plus `kubectl rollout restart deployment/aggregation-epp`) before each run.
