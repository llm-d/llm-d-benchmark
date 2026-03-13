# vLLM Empirical Memory Profiling Results

Test environment: H100 GPU (79.18 GiB), vLLM with FlashAttention, `VLLM_LOGGING_LEVEL=DEBUG`.

All tests use `--enable-prefix-caching --block-size=128`. Default `--gpu-memory-utilization=0.9` unless noted.

## Summary

| Model | Weights | Activation | Non-torch | CUDA Graph | KV Cache | TP | Util | max-model-len |
| ----- | ------- | ---------- | --------- | ---------- | -------- | -- | ---- | ------------- |
| gpt-oss-20b (MoE) | 13.47 | 7.38 | 0.13 | 0.39 | 50.28 | 1 | 0.9 | 16000 |
| gpt-oss-120b (MoE) | 64.38 | 7.38 | 0.13 | 1.03 | 3.33 | 1 | 0.9 | 16000 |
| Llama-3.3-70B-FP8 | 33.88 | 4.84 | 0.55 | -0.42 | 32.00 | 2 | 0.9 | 16000 |
| Llama-3.1-8B | 14.99 | 4.76 | 0.13 | -0.45 | 51.38 | 1 | 0.9 | 16000 |
| Qwen3-0.6B | 1.12 | 5.56 | 0.13 | 0.10 | 64.45 | 1 | 0.9 | 16000 |
| Qwen3-32B | 61.03 | 5.64 | 0.14 | -0.88 | 4.45 | 1 | 0.9 | 16000 |
| Qwen3-32B | 30.59 | 5.64 | 0.54 | -0.33 | 34.49 | 2 | 0.9 | 16000 |
| Mistral-Small-3.2-24B | 44.76 | 2.12 | 0.14 | -0.76 | 28.20 | 1 | 0.95 | 16000 |

All values in GiB. "Activation" = torch peak memory increase. "CUDA Graph" = memory change during graph capture (negative = freed).

### Failed Configurations

| Model | TP | Failure | Root Cause |
| ----- | -- | ------- | ---------- |
| Deepseek-R1 (FP8) | 1 | OOM during load | Weights exceeded single GPU; needs TP |
| Llama-3.3-70B-FP8 | 1 | No KV cache room | 67.72 GiB weights, -1.44 GiB remaining; use TP=2 |
| Qwen3-32B | 1 | No KV cache room | 61.03 GiB weights at max-model-len=32000; use TP=2 or reduce context |

## Key Patterns

**Activation memory is constant per model type** (independent of max-model-len and batch-size):
- Multimodal: ~2.1 GiB (vision encoder skips CUDA graph capture)
- Dense text-only: ~4.8-5.6 GiB
- MoE: ~7.4 GiB

**Non-torch memory** scales with TP: ~0.13 GiB (TP=1), ~0.55 GiB (TP=2).

**CUDA graph memory** ranges from -0.88 to +1.03 GiB. Negative values (memory freed) are common for large dense models.

**Activation is constant across context lengths**: Qwen3-0.6B at max-model-len=16000 and max-model-len=32000 both measured 5.56 GiB activation and 64.45 GiB KV cache.

## Per-Model Notes

### gpt-oss-20b / gpt-oss-120b (MoE)

- **Model:** openai/gpt-oss-20b, openai/gpt-oss-120b
- MoE models have the highest activation memory (~7.38 GiB) due to expert routing overhead
- gpt-oss-120b barely fits on a single H100 (64.38 GiB weights, only 3.33 GiB for KV cache)

### Llama-3.3-70B-FP8

- **Model:** RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic
- Requires TP=2 (67.72 GiB weights at TP=1 leaves no room for KV cache)
- At TP=2: 33.88 GiB weights per GPU, 32.0 GiB KV cache available

### Llama-3.1-8B

- **Model:** meta-llama/Llama-3.1-8B-Instruct
- Small footprint (14.99 GiB), generous KV cache (51.38 GiB)

### Qwen3-0.6B / Qwen3-32B

- **Models:** Qwen/Qwen3-0.6B, Qwen/Qwen3-32B
- Qwen3-0.6B: smallest model tested, 64.45 GiB KV cache available
- Qwen3-32B at TP=1: only 4.45 GiB KV cache (tight); TP=2 gives 34.49 GiB

### Mistral-Small-3.2-24B

- **Model:** mistralai/Mistral-Small-3.2-24B-Instruct-2506
- **Architecture:** Mistral3ForConditionalGeneration (multimodal / vision-language)
- **vLLM:** v0.11.0 (V1 engine), `--gpu-memory-utilization=0.95`, `--tokenizer-mode=mistral --config-format=mistral --load-format=mistral`
- **Notable:** Lowest activation memory measured (2.12 GiB), likely because vision encoder does not participate in CUDA graph capture

**Model architecture:** GQA, 40 layers, 32 attention heads, 8 KV heads, head_dim=128, hidden_size=5120

**KV cache validation** -- per-token formula matches vLLM exactly:

```
Per-token KV = num_layers x 2 x head_dim x num_kv_heads x dtype_bytes
             = 40 x 2 x 128 x 8 x 2 = 163,840 bytes (160 KB/token)

vLLM empirical: 28.20 GiB / 184,832 tokens = 163,840 bytes/token  (exact match)
```

**Live request validation** (15,049 tokens, measured via Prometheus /metrics):

| Metric | Measured | Expected |
| ------ | -------- | -------- |
| KV cache usage | 8.18% | 8.17% (118 blocks / 1,444 total) |
| Blocks allocated | 118 | ceil(15,049 / 128) = 118 |
| Prompt throughput | ~1,481 tok/s | -- |
| Prefix cache hit rate | 30% | -- |

**Capacity planner accuracy** (before/after adding validated activation profiles):

| Metric | Before | After | vLLM Actual |
| ------ | ------ | ----- | ----------- |
| Activation estimate | 5.5 GiB | 2.5 GiB | 2.12 GiB |
| Available KV cache | 24.82 GiB | 27.82 GiB | 28.20 GiB |
| Error | -3.38 GiB | **-0.38 GiB** | -- |
| Max concurrent @16K | 10.2x | **11.4x** | 11.55x |

## How to Replicate

### Setup

Requirements: Kubernetes cluster with H100 GPU nodes, HuggingFace token secret.

Deploy a vLLM pod with `VLLM_LOGGING_LEVEL=DEBUG`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: vllm-profiling
spec:
  restartPolicy: Never
  containers:
    - name: vllm
      image: vllm/vllm-openai:v0.11.0
      command: ["vllm", "serve"]
      args:
        - <model-name>                    # e.g. Qwen/Qwen3-32B
        - --tensor-parallel-size=<tp>     # 1 or 2
        - --gpu-memory-utilization=0.90
        - --max-model-len=16000
        - --block-size=128
        - --enable-prefix-caching
        - --host=0.0.0.0
        - --port=8000
      resources:
        requests:
          nvidia.com/gpu: "<tp>"          # must match tensor-parallel-size
        limits:
          nvidia.com/gpu: "<tp>"
      env:
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef: { name: llm-d-hf-token, key: HF_TOKEN }
        - name: VLLM_LOGGING_LEVEL
          value: DEBUG
        - name: HF_HOME
          value: /tmp/cache
      volumeMounts:
        - { name: cache, mountPath: /tmp/cache }
  volumes:
    - { name: cache, emptyDir: {} }
```

Wait for "Application startup complete" in logs.

### Extract Metrics

Search the pod logs for these strings:

| Log substring | What it gives you |
| ------------- | ----------------- |
| `"Model loading took"` | Weight memory (GiB) and load time |
| `"torch peak memory increase"` | Activation memory (GiB) |
| `"non-torch forward increase memory"` | Non-torch memory (GiB) |
| `"Available KV cache memory"` | KV cache allocation (GiB) |
| `"Free memory on device"` | Total/free GPU memory at startup |
| `"GPU KV cache size"` | Total KV cache tokens and block count |
| `"Maximum concurrency for"` | Max concurrent requests at max-model-len |

### Validate KV Cache at Runtime

```bash
# Port-forward to the pod
kubectl port-forward pod/<name> -n <ns> 8000:8000 &

# Send a request and check metrics
curl -X POST localhost:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"<model>","messages":[{"role":"user","content":"<long prompt>"}],"max_tokens":10}'

# Check KV cache usage
curl -s localhost:8000/metrics | grep kv_cache_usage_perc
```
