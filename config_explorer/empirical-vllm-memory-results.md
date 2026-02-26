# vLLM Empirical Test Results Analysis

Analysis of vLLM log files for various models tested on H100 GPUs (79.18 GiB total memory).

## Summary Table

| Model                    | Status  | Model Weight (GiB) | Peak Activation (GiB) | Non-torch Memory (GiB) | CUDAGraph Memory (GiB) | Available KV Cache (GiB) | TP Size | Max Model Len |
| ------------------------ | ------- | ------------------ | --------------------- | ---------------------- | ---------------------- | ------------------------ | ------- | ------------- |
| Deepseek-R1              | FAILED  | N/A                | N/A                   | N/A                    | N/A                    | N/A                      | 1       | 16000         |
| gpt-oss-20b              | SUCCESS | 13.47              | 7.38                  | 0.13                   | 0.39                   | 50.28                    | 1       | 16000         |
| gpt-oss-120b             | SUCCESS | 64.38              | 7.38                  | 0.13                   | 1.03                   | 3.33                     | 1       | 16000         |
| Llama-3.3-70B-FP8 (TP=2) | SUCCESS | 33.88              | 4.84                  | 0.55                   | -0.42                  | 32.0                     | 2       | 16000         |
| Llama-3.3-70B-FP8 (TP=1) | FAILED  | 67.72              | N/A                   | N/A                    | N/A                    | -1.44                    | 1       | 16000         |
| Llama-3.1-8B             | SUCCESS | 14.99              | 4.76                  | 0.13                   | -0.45                  | 51.38                    | 1       | 16000         |
| Qwen3-0.6B               | SUCCESS | 1.12               | 5.56                  | 0.13                   | 0.10                   | 64.45                    | 1       | 16000         |
| Qwen3-0.6B               | SUCCESS | 1.12               | 5.56                  | 0.13                   | 0.10                   | 64.45                    | 1       | 32000         |
| Qwen3-32B                | FAILED  | 61.03              | 5.64                  | 0.14                   | N/A                    | N/A                      | 1       | 32000         |
| Qwen3-32B                | SUCCESS | 61.03              | 5.64                  | 0.14                   | -0.88                  | 4.45                     | 1       | 16000         |
| Qwen3-32B                | SUCCESS | 30.59             | 5.64                  | 0.54                  | -0.33                  | 34.49                     | 2       | 16000         |
| Mistral-Small-3.2-24B    | SUCCESS | 44.76              | 2.12                  | 0.14                   | -0.76                  | 28.20                    | 1       | 16000         |
---

## Detailed Results

### 1. Deepseek-R1 (deepseek-ai/DeepSeek-R1)

**Status:** ENGINE FAILED - Out of Memory

#### Model Configuration
- **Model name:** deepseek-ai/DeepSeek-R1
- **max-model-len:** 16000
- **tensor-parallel-size:** 1
- **gpu-memory-utilization:** 0.9 (default)
- **quantization:** fp8
- **enable-prefix-caching:** True

#### Empirical Results
- **Model loading:** FAILED during loading
- **Available KV cache memory:** N/A (engine failed before allocation)
- **Free memory on device:** N/A (engine failed before reporting)

#### Memory Metrics
- **Pre-failure state:** 78.57 GiB free, 71.26 GiB requested
- **Failure point:** Tried to allocate 3.50 GiB but only 3.33 GiB was free
- **Memory in use at failure:** 75.84 GiB total, 75.16 GiB by PyTorch

#### Notes
Model failed to load on a single H100 GPU. Failed during DeepseekV2MoE layer initialization with FP8 quantization. Requires tensor parallelism or larger GPU.

---

### 2. gpt-oss-20b (openai/gpt-oss-20b)

**Status:** SUCCESS

#### Model Configuration
- **Model name:** openai/gpt-oss-20b
- **max-model-len:** 16000
- **tensor-parallel-size:** 1
- **gpu-memory-utilization:** 0.9
- **enable-prefix-caching:** True

#### Empirical Results
- **Model loading took:** 13.47 GiB memory and 31.68 seconds
- **Available KV cache memory:** 50.28 GiB
- **Free memory on device:** 78.57/79.18 GiB on startup

#### Memory Metrics
- **Weight memory:** 13.47 GiB
- **Peak activation memory:** 7.38 GiB
- **Non-torch memory:** 0.13 GiB
- **CUDAGraph memory:** 0.39 GiB
- **KV cache memory:** 50.28 GiB
- **Desired GPU utilization:** 0.9 (71.26 GiB)

#### Recommendations
- For requested memory: `--kv-cache-memory=53414341735` (49.75 GiB)
- For full GPU utilization: `--kv-cache-memory=61267232256` (57.06 GiB)

---

### 3. Llama-3.3-70B-Instruct-FP8-dynamic (TP=2)

**Status:** SUCCESS

#### Model Configuration
- **Model name:** RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic
- **max-model-len:** 16000
- **tensor-parallel-size:** 2
- **gpu-memory-utilization:** 0.9 (default)
- **enable-prefix-caching:** True

#### Empirical Results
- **Model loading took:** 33.88 GiB memory and 116.61 seconds
- **Available KV cache memory:** 32.0 GiB
- **Free memory on device:** 77.64/79.18 GiB on startup

#### Memory Metrics (per device with TP=2)
- **Weight memory:** 33.88 GiB
- **Peak activation memory:** 4.84 GiB
- **Non-torch memory:** 0.55 GiB
- **CUDAGraph memory:** -0.42 GiB
- **KV cache memory:** 32.0 GiB
- **Desired GPU utilization:** 0.9 (71.26 GiB)

#### Recommendations
- For requested memory: `--kv-cache-memory=34644505703` (32.27 GiB)
- For full GPU utilization: `--kv-cache-memory=41499086336` (38.65 GiB)

---

### 4. Llama-3.3-70B-Instruct-FP8-dynamic (TP=1)

**Status:** ENGINE FAILED - Insufficient KV Cache Memory

#### Model Configuration
- **Model name:** RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic
- **max-model-len:** 16000
- **tensor-parallel-size:** 1
- **gpu-memory-utilization:** 0.9 (default)
- **quantization:** compressed-tensors (FP8)
- **enable-prefix-caching:** True

#### Empirical Results
- **Model loading took:** 67.72 GiB memory and 45.09 seconds
- **Available KV cache memory:** -1.44 GiB (NEGATIVE - INSUFFICIENT)
- **Free memory on device:** Not reported (engine failed)

#### Memory Metrics
- **Weight memory:** 67.72 GiB
- **Peak activation memory:** 4.84 GiB
- **Non-torch memory:** 0.14 GiB
- **CUDAGraph memory:** 0.6 GiB
- **KV cache memory:** -1.44 GiB (insufficient)

#### Notes
Model weights loaded successfully but consumed too much memory (67.72 GiB), leaving no room for KV cache. Error: `ValueError: No available memory for the cache blocks. Try increasing gpu_memory_utilization when initializing the engine.`

**Solutions:**
- Use tensor parallelism (TP=2 works as shown above)
- Reduce max-model-len
- Use GPU with more memory

---

### 5. Llama-3.1-8B-Instruct (meta-llama/Llama-3.1-8B-Instruct)

**Status:** SUCCESS

#### Model Configuration
- **Model name:** meta-llama/Llama-3.1-8B-Instruct
- **max-model-len:** 16000
- **tensor-parallel-size:** 1
- **gpu-memory-utilization:** 0.9
- **enable-prefix-caching:** True

#### Empirical Results
- **Model loading took:** 14.99 GiB memory and 31.46 seconds
- **Available KV cache memory:** 51.38 GiB
- **Free memory on device:** 78.57/79.18 GiB on startup

#### Memory Metrics
- **Weight memory:** 14.99 GiB
- **Peak activation memory:** 4.76 GiB
- **Non-torch memory:** 0.13 GiB
- **CUDAGraph memory:** -0.45 GiB
- **KV cache memory:** 51.38 GiB
- **Desired GPU utilization:** 0.9 (71.26 GiB)

#### Recommendations
- For requested memory: `--kv-cache-memory=55491753575` (51.68 GiB)
- For full GPU utilization: `--kv-cache-memory=63344644096` (58.99 GiB)

---

### 6. Qwen3-0.6B (Qwen/Qwen3-0.6B)

**Status:** SUCCESS

#### Model Configuration
- **Model name:** Qwen/Qwen3-0.6B
- **max-model-len:** 16000
- **tensor-parallel-size:** 1
- **gpu-memory-utilization:** 0.9
- **enable-prefix-caching:** True

#### Empirical Results
- **Model loading took:** 1.12 GiB memory and 16.54 seconds
- **Available KV cache memory:** 64.45 GiB
- **Free memory on device:** 78.57/79.18 GiB on startup

#### Memory Metrics
- **Weight memory:** 1.12 GiB
- **Peak activation memory:** 5.56 GiB
- **Non-torch memory:** 0.13 GiB
- **CUDAGraph memory:** 0.10 GiB
- **KV cache memory:** 64.45 GiB
- **Desired GPU utilization:** 0.9 (71.26 GiB)

#### Recommendations
- For requested memory: `--kv-cache-memory=68930180199` (64.2 GiB)
- For full GPU utilization: `--kv-cache-memory=76783070720` (71.51 GiB)
---

### 6. Qwen3-0.6B (Qwen/Qwen3-0.6B)

**Status:** SUCCESS

#### Model Configuration
- **Model name:** Qwen/Qwen3-0.6B
- **max-model-len:** 32000
- **tensor-parallel-size:** 1
- **gpu-memory-utilization:** 0.9
- **enable-prefix-caching:** True

#### Empirical Results
- **Model loading took:** 1.12 GiB memory and 16.45 seconds
- **Available KV cache memory:** 64.45 GiB
- **Free memory on device:** 78.57/79.18 GiB on startup

#### Memory Metrics
- **Weight memory:** 1.12 GiB
- **Peak activation memory:** 5.56 GiB
- **Non-torch memory:** 0.13 GiB
- **CUDAGraph memory:** 0.10 GiB
- **KV cache memory:** 64.45 GiB
- **Desired GPU utilization:** 0.9 (71.26 GiB)

#### Recommendations
- For requested memory: `--kv-cache-memory=68932277351` (64.2 GiB)
- For full GPU utilization: `--kv-cache-memory=76785167872` (71.51 GiB)

---

### 10. Mistral-Small-3.2-24B-Instruct-2506 (mistralai/Mistral-Small-3.2-24B-Instruct-2506)

**Status:** SUCCESS

#### Model Configuration
- **Model name:** mistralai/Mistral-Small-3.2-24B-Instruct-2506
- **Architecture:** Mistral3ForConditionalGeneration (multimodal / vision-language)
- **vLLM version:** 0.11.0 (V1 engine)
- **max-model-len:** 16000
- **tensor-parallel-size:** 1
- **gpu-memory-utilization:** 0.95
- **dtype:** bfloat16
- **block-size:** 128
- **tokenizer-mode:** mistral
- **config-format:** mistral
- **load-format:** mistral
- **enable-prefix-caching:** True
- **chunked-prefill:** enabled (max_num_batched_tokens=8192)

#### Model Architecture
- **Attention type:** GQA (Grouped-query attention)
- **Hidden layers:** 40
- **Attention heads:** 32
- **KV heads:** 8
- **Head dimension:** 128
- **Hidden size:** 5120
- **Vocab size:** 131,072
- **Max position embeddings:** 131,072

#### Empirical Results
- **Model loading took:** 44.76 GiB memory and 49.79 seconds
- **Weight download time:** 41.89 seconds
- **Available KV cache memory:** 28.20 GiB
- **Free memory on device:** 78.59/79.19 GiB on startup
- **GPU KV cache size:** 184,832 tokens (1,444 blocks x 128 tokens/block)
- **Maximum concurrency for 16,000 tokens per request:** 11.55x

#### Memory Metrics
- **Weight memory:** 44.76 GiB
- **Peak activation memory:** 2.12 GiB
- **Non-torch memory:** 0.14 GiB
- **CUDAGraph memory:** -0.76 GiB
- **KV cache memory:** 28.20 GiB
- **Total non-KV cache memory:** 47.02 GiB
- **Desired GPU utilization:** 0.95 (75.23 GiB)

#### KV Cache Validation
Per-token KV cache formula validated against vLLM block allocation:

```
Per-token KV cache = num_layers × 2 × head_dim × num_kv_heads × dtype_bytes
                   = 40 × 2 × 128 × 8 × 2
                   = 163,840 bytes (160 KB/token)

Empirical: 28.20 GiB / 184,832 tokens = 163,840 bytes/token ✓ (exact match)
```

Live request validation (15,049 total tokens):
- **Peak KV cache usage:** 8.18% (from Prometheus /metrics endpoint)
- **Blocks allocated:** 118 (= ceil(15,049 / 128))
- **Expected usage:** 118 / 1,444 = 8.17% ✓
- **Prompt throughput:** ~1,481 tokens/s
- **Generation throughput:** ~23.7 tokens/s
- **Prefix cache hit rate:** 30% (after 3 requests with overlapping content)

Extrapolated to 16,000 tokens:
- **Blocks needed:** 125 (= 16,000 / 128)
- **KV cache per request:** 125 / 1,444 × 28.20 GiB = **2.44 GiB**
- **Max concurrent requests:** 28.20 / 2.44 = **11.55x** ✓ (matches vLLM startup log)

#### Capacity Planner Accuracy
| Metric | Planner (before) | Planner (after) | vLLM Empirical |
| ------ | ---------------- | --------------- | -------------- |
| Activation memory | 5.5 GiB | 2.5 GiB | 2.12 GiB |
| Available KV cache | 24.82 GiB | 27.82 GiB | 28.20 GiB |
| KV cache error | -3.38 GiB | -0.38 GiB | -- |
| Max concurrent @16K | 10.2x | 11.4x | 11.55x |

The validated activation profile (2.5 GiB) reduced the estimation error from 3.38 GiB to 0.38 GiB (9x more accurate).

#### Recommendations
- For requested memory: `--kv-cache-memory=30941080576` (28.82 GiB)
- For full GPU utilization: `--kv-cache-memory=34545658880` (32.17 GiB)

#### Notes
This is a multimodal (vision) model using Mistral's custom tokenizer and config format. Despite being a 24B model, the BF16 weights are large at 44.76 GiB. The gpu-memory-utilization was set to 0.95 (higher than the typical 0.9 used for other models). Peak activation memory is notably low (2.12 GiB) compared to other dense models (4.76-5.64 GiB), likely because the vision encoder does not participate in CUDA graph capture. The CUDAGraph memory is -0.76 GiB (memory freed during graph capture), which is consistent with other large dense models (Qwen3-32B: -0.88 GiB).

---

## Key Insights

### Successful Models
1. **Qwen3-0.6B**: Smallest memory footprint (1.12 GiB weights), highest KV cache availability (64.45 GiB)
2. **gpt-oss-20b**: Moderate size (13.47 GiB weights), good KV cache (50.28 GiB)
3. **Llama-3.1-8B**: Similar to gpt-oss-20b (14.99 GiB weights, 51.38 GiB KV cache)
4. **Llama-3.3-70B-FP8 (TP=2)**: Large model successful with tensor parallelism (33.88 GiB per GPU)
5. **Mistral-Small-3.2-24B**: BF16 multimodal model (44.76 GiB weights), moderate KV cache (28.20 GiB) with 0.95 utilization

### Failed Models
1. **Deepseek-R1**: OOM during model loading with FP8 quantization
2. **Llama-3.3-70B-FP8 (TP=1)**: Model loaded (67.72 GiB) but insufficient memory for KV cache

### Memory Pattern Observations
- **Non-torch memory:** Consistently around 0.13-0.55 GiB across models
- **Peak activation memory:** Ranges from 2.12-7.38 GiB for successful models
  - Multimodal models: 2.12 GiB (Mistral-Small-3.2-24B) — significantly lower than text-only models
  - Dense text-only models: 4.76-5.64 GiB (Llama-8B, Llama-70B, Qwen3-0.6B, Qwen3-32B)
  - MoE models: 7.38 GiB (gpt-oss-20b)
- **CUDAGraph memory:** Small or negative (optimization), ranging from -0.88 to 0.39 GiB
- **Tensor Parallelism benefit:** Llama-3.3-70B requires TP=2 to fit in H100 (33.88 GiB per GPU vs 67.72 GiB for TP=1)

### Hardware Utilization
- **GPU:** H100 with 79.18 GiB total memory
- **Typical free memory at startup:** 78.57 GiB
- **Target utilization:** 0.9 (71.26 GiB)
- **Largest successful single-GPU model:** Llama-3.1-8B / gpt-oss-20b (~15 GiB weights)
- **Largest model overall:** Llama-3.3-70B-FP8 with TP=2

---

## How to Replicate These Results

### Prerequisites
- Kubernetes cluster with H100 GPU nodes
- Access to a namespace with GPU resources
- HuggingFace token stored as a Kubernetes secret (for gated models)
- vLLM container image: `vllm/vllm-openai:latest`

### Step 1: Create Kubernetes Pod Configuration

Create a pod YAML file based on the following template:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: <pod-name>
  namespace: <your-namespace>
spec:
  restartPolicy: Never
  containers:
    - name: vllm
      image: vllm/vllm-openai:latest
      command: ["vllm", "serve"]
      args:
        - <model-name>  # e.g., Qwen/Qwen3-32B
        - --tensor-parallel-size=<tp-size>  # 1 or 2
        - --gpu-memory-utilization=0.90
        - --max-model-len=<max-len>  # e.g., 16000 or 32000
        - --block-size=128
        - --enable-prefix-caching
        - --host=0.0.0.0
        - --port=8000
      ports:
        - containerPort: 8000
          name: http
          protocol: TCP
      volumeMounts:
        - name: cache
          mountPath: /tmp/cache
      resources:
        requests:
          cpu: '32'
          memory: '128Gi'
          nvidia.com/gpu: "<gpu-count>"  # Match tensor-parallel-size
        limits:
          nvidia.com/gpu: "<gpu-count>"
          cpu: '32'
          memory: '128Gi'
      env:
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: llm-d-hf-token  # Your HF token secret name
              key: HF_TOKEN
        - name: VLLM_LOGGING_LEVEL
          value: DEBUG
        - name: HF_HOME
          value: /tmp/cache
        - name: TRANSFORMERS_CACHE
          value: /tmp/cache
        - name: XDG_CACHE_HOME
          value: /tmp/cache
        - name: XDG_CONFIG_HOME
          value: /tmp/cache
        - name: HOME
          value: /tmp/cache
  volumes:
    - name: cache
      emptyDir: {}
```

### Step 2: Configure for Each Model

Adjust the following parameters for each model you want to test:

1. **Model name** (in args): Use the full HuggingFace model path
   - Examples: `Qwen/Qwen3-0.6B`, `meta-llama/Llama-3.1-8B-Instruct`, `openai/gpt-oss-20b`

2. **Tensor parallel size**: Set based on model size
   - Small models (< 20B): `--tensor-parallel-size=1`, `nvidia.com/gpu: "1"`
   - Large models (70B+): `--tensor-parallel-size=2`, `nvidia.com/gpu: "2"`

3. **Max model length**: Adjust based on your test scenario
   - Standard: `--max-model-len=16000`
   - Extended: `--max-model-len=32000`

### Step 3: Deploy and Capture Logs

1. Create the HuggingFace token secret (if not already exists):
   ```bash
   kubectl create secret generic llm-d-hf-token \
     --from-literal=HF_TOKEN=<your-token> \
     -n <your-namespace>
   ```

2. Deploy the pod:
   ```bash
   kubectl apply -f <your-pod-config>.yaml
   ```

3. Stream and capture logs:
   ```bash
   kubectl logs -f <pod-name> -n <your-namespace> > <model-name>.log
   ```

4. Wait for the model to either:
   - Successfully start (logs show "Avg prompt throughput" or server ready)
   - Fail to initialize (OOM error or insufficient memory error)

### Step 4: Extract Memory Metrics from Logs

Search for the following key information in each log file:

1. **Model Configuration** (search for "non-default args:"):
   - Model name
   - max-model-len
   - tensor-parallel-size
   - gpu-memory-utilization

2. **Memory Metrics** (search for these exact substrings):
   - `"Model loading took"`: Extract weight memory (GiB) and loading time
   - `"Available KV cache memory:"`: Extract KV cache allocation (GiB)
   - `"Free memory on device"`: Extract free/total GPU memory on startup

3. **Failure Information** (if engine fails):
   - Error messages containing "Out of memory" or "CUDA out of memory"
   - `ValueError: No available memory for the cache blocks`
   - Memory state at failure point

### Notes
- **VLLM_LOGGING_LEVEL=DEBUG** is critical for capturing detailed memory metrics
- Log files can be very long (thousands of lines); use grep/search to find relevant sections
- Some models may require specific quantization settings or may not support certain features
- Memory values may vary slightly between runs due to caching and initialization differences
