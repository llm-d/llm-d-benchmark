# Complex Configuration Patterns

This document contains patterns for handling complex configurations when converting llm-d guides.

## Table of Contents

- [Extra Args and Commands](#extra-args-and-commands)
- [Extra Volumes and Mounts](#extra-volumes-and-mounts)
- [Environment Variables](#environment-variables)
- [GAIE Custom Plugin Configuration](#gaie-custom-plugin-configuration)
- [Accelerator Patterns](#accelerator-patterns)
  - [XPU (Intel GPU)](#xpu-intel-gpu)
  - [P/D (Prefill/Decode) Disaggregation](#pd-prefilldecode-disaggregation)

## Extra Args and Commands

**IMPORTANT**: Always use the standard llm-d-benchmark pattern:

```bash
export LLMDBENCH_VLLM_COMMON_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM
EOF
```

The preprocess command and vllm serve are REQUIRED and must come first, regardless of what the guide specifies.

## Extra Volumes and Mounts

**IMPORTANT**: Always include the preprocesses volume and mount:

```bash
export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES}
- name: preprocesses
  configMap:
    defaultMode: 0755
    name: llm-d-benchmark-preprocesses
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM
<...additional volumes from guide...>
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
- name: preprocesses
  mountPath: /setup/preprocess
<...additional volume mounts from guide...>
EOF
```

The preprocesses configMap volume should be listed FIRST. The preprocesses mount can be anywhere in the list.

## Environment Variables

**CRITICAL RULE**: ALL environment variables defined in the guide's `env:` section MUST be captured in the scenario file. Never silently drop env vars - they are often essential for the guide to function correctly (e.g., accelerator-specific settings, logging paths, feature flags).

For container environment variables:

```bash
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML
- name: VLLM_LOGGING_LEVEL
  value: INFO
- name: UCX_TLS
  value: "sm,cuda_ipc,cuda_copy,tcp"
EOF
```

### Mapping

| Guide Section | LLMDBENCH Variable |
|--------------|-------------------|
| `decode.containers[].env` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML` |
| `prefill.containers[].env` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ENVVARS_TO_YAML` |

### Verification

After generating the scenario file, verify:
1. Count the env vars in the source guide's `env:` sections
2. Count the env vars in the generated `ENVVARS_TO_YAML` blocks
3. The counts must match (excluding any benchmark-framework-added vars which should be documented)

## GAIE Custom Plugin Configuration

**CRITICAL RULE**: When the guide contains `inferenceExtension.pluginsCustomConfig`, you MUST always define `LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS` in the scenario file, regardless of whether a preset file exists.

### Workflow

1. **Extract Custom Config**: If `inferenceExtension.pluginsCustomConfig` exists in the guide's GAIE values.yaml, extract the full YAML content
2. **Always Include in Scenario**: Generate the `LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS` variable with the extracted content
3. **Set Plugin Config File**: Also set `LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE` to the filename specified in `inferenceExtension.pluginsConfigFile`
4. **Check for Preset Conflicts**: After generating the scenario, check if a preset file exists at `setup/presets/gaie/<filename>` with similar content
5. **Issue Warning (Optional)**: If a preset exists but the experiment doesn't use it, add a comment warning in the scenario file

### Template

```bash
# GAIE configuration
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="<filename-from-guide>.yaml"

# Custom plugin configuration from guide
# NOTE: A preset file may exist at setup/presets/gaie/<filename>.yaml
#       but this guide defines custom inline config which takes precedence
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS
<filename>.yaml: |
  <full-yaml-content-from-pluginsCustomConfig>
EOF
```

### Example

```bash
# GAIE configuration
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="precise-prefix-cache-config.yaml"

export LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS
precise-prefix-cache-config.yaml: |
  apiVersion: inference.networking.x-k8s.io/v1alpha1
  kind: EndpointPickerConfig
  plugins:
    - type: single-profile-handler
    - type: precise-prefix-cache-scorer
      parameters:
        tokenProcessorConfig:
          blockSize: 64
        indexerConfig:
          tokenizersPoolConfig:
            modelName: "Qwen/Qwen3-32B"
            hf:
              tokenizersCacheDir: "/tmp/tokenizers"
        kvEventsConfig:
          topicFilter: "kv@"
          concurrency: 4
          discoverPods: false
          zmqEndpoint: "tcp://*:5557"
    - type: kv-cache-utilization-scorer
    - type: queue-scorer
    - type: max-score-picker
  schedulingProfiles:
    - name: default
      plugins:
        - pluginRef: precise-prefix-cache-scorer
          weight: 3.0
        - pluginRef: kv-cache-utilization-scorer
          weight: 2.0
        - pluginRef: queue-scorer
          weight: 2.0
        - pluginRef: max-score-picker
EOF
```

### Why This Matters

1. **Guide Authority**: The guide's custom config is authoritative - it represents the exact configuration needed for that specific guide
2. **Preset Independence**: Preset files may not match exactly, or may not exist at all
3. **Experiment Variations**: If the experiment varies plugin configs, that's a separate concern - the base scenario should always include what the guide defines
4. **Self-Contained Scenarios**: Scenarios should be self-contained and not rely on external preset files unless explicitly intended

### Do NOT

- Skip custom plugin config just because a preset file exists
- Assume preset files will have the same content as the guide's custom config
- Omit custom config to avoid "duplication" - the scenario file should be complete

## LeaderWorkerSet / Multinode Patterns

When converting guides that use LeaderWorkerSet (LWS) for multi-node or multi-pod deployment:

### Detection

A guide uses LeaderWorkerSet if:
- The kustomize manifests contain `kind: LeaderWorkerSet` resources
- The manifest has fields like `leaderWorkerTemplate`, `workerTemplate`, `size`, or `LWS_*` environment variables
- The vLLM command uses flags like `--data-parallel-address`, `--data-parallel-start-rank`, `--data-parallel-rpc-port`

### Support

**LeaderWorkerSet IS supported** by the llm-d-benchmark framework via the modelservice Helm chart. Set:

```bash
export LLMDBENCH_VLLM_MODELSERVICE_MULTINODE=true
```

This maps to `multinode: true` in the modelservice Helm chart, which enables LeaderWorkerSet-based deployment.

### Configuration Mapping

| LWS Manifest Field | LLMDBENCH Variable | Notes |
|-------------------|-------------------|-------|
| `spec.replicas` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS` | Number of LWS groups |
| `spec.leaderWorkerTemplate.size` | `LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM` | Pods per LWS group |
| `DP_SIZE_LOCAL` env var | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM` | Data parallel per pod |
| `TP_SIZE` env var | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM` | Tensor parallel size |

### Template

```bash
# =============================================================================
# LeaderWorkerSet / Multinode Configuration
# SOURCE: <path-to-lws-manifest>
# Lines <line-numbers>:
#   spec.replicas: <value>
#   spec.leaderWorkerTemplate.size: <value>
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_MULTINODE=true

# Number of LWS groups (each group has size workers)
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=<replicas>

# Number of pods per LWS group
export LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM=<lws-size>

# Data parallelism per pod
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM=<dp_size_local>
```

### LWS-Specific vLLM Arguments

When multinode is enabled, the modelservice Helm chart automatically handles LWS-specific vLLM arguments. You typically do NOT need to include these in `EXTRA_ARGS`:
- `--data-parallel-address` (set automatically from LWS leader)
- `--data-parallel-start-rank` (set automatically per pod)
- `--data-parallel-rpc-port` (set automatically)

However, DO include these parallelism flags in `EXTRA_ARGS`:
- `--tensor-parallel-size`
- `--data-parallel-size-local` (maps to `DP_SIZE_LOCAL`)
- `--data-parallel-size` (total DP = `LWS_GROUP_SIZE * DP_SIZE_LOCAL`)

### Complete Example

```bash
# Enable LeaderWorkerSet deployment
export LLMDBENCH_VLLM_MODELSERVICE_MULTINODE=true

# LWS group configuration
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1      # 1 LWS group
export LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM=2    # 2 pods per group

# Per-pod parallelism
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM=8  # 8 GPUs per pod

# Total: 1 group × 2 pods × 8 GPUs = 16 GPUs for decode
```

### DO NOT

- Add comments saying LWS is "not supported" by llm-d-benchmark
- Skip multinode configuration when converting LWS-based guides
- Manually set LWS-specific args that are auto-configured by the Helm chart

## Accelerator Patterns

### XPU (Intel GPU)

When converting guides for Intel XPU accelerators:

**Accelerator Resources:**
- `intel-i915`: Data Center GPU Max 1550
- `intel-xe`: Battlemage series

**Required Environment Variables:**
```bash
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML
- name: VLLM_USE_V1
  value: "1"
- name: TORCH_LLM_ALLREDUCE
  value: "1"
- name: VLLM_WORKER_MULTIPROC_METHOD
  value: "spawn"
- name: UCX_TLS
  value: "tcp"
EOF
```

**Notes:**
- XPU guides typically use smaller models (e.g., Qwen3-0.6B) due to memory constraints
- Set `LLMDBENCH_VLLM_COMMON_ACCELERATOR_NAME` to the appropriate Intel GPU type

### P/D (Prefill/Decode) Disaggregation

When converting guides that use prefill/decode disaggregation:

**Stage Roles:**
- **Decode stage**: Uses `kv_consumer` role
- **Prefill stage**: Uses `kv_producer` role

**KV Transfer Configuration:**
Both stages need identical KV transfer config pointing to NixlConnector:
```bash
--kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer",...}'  # decode
--kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer",...}'  # prefill
```

**GAIE Routing:**
- P/D guides typically use `pd-config.yaml` for GAIE plugin configuration
- This config routes requests between prefill and decode stages
- Set `LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="pd-config.yaml"`

**Replica Configuration:**
```bash
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=<decode-count>
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=<prefill-count>
```

**Complete P/D Example:**

```bash
# Decode stage
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM \
--kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_parallel_size":1,"kv_buffer_size":1e9}'
EOF

# Prefill stage
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PREPROCESS; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM \
--kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_parallel_size":1,"kv_buffer_size":1e9}'
EOF

# GAIE for P/D routing
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="pd-config.yaml"
```
