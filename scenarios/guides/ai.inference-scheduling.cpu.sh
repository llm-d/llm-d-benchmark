#!/bin/bash
# Generated scenario for llm-d guide: inference-scheduling (CPU variant)
# Source: https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling
# Files: ms-inference-scheduling/values_cpu.yaml, gaie-inference-scheduling/values.yaml
#
# This scenario configures inference scheduling with CPU acceleration using the
# llm-d inference scheduler extension for intelligent workload routing based on
# KV cache utilization metrics.

# =============================================================================
# Model Configuration
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 5, 8:
#   uri: "hf://meta-llama/Llama-3.2-3B-Instruct"
#   name: "meta-llama/Llama-3.2-3B-Instruct"
# =============================================================================
export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.2-3B-Instruct"

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Line 6:
#   size: 100Gi
# =============================================================================
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE="100Gi"

# =============================================================================
# Accelerator Configuration
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Line 16:
#   type: cpu
# =============================================================================
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE="cpu"

# =============================================================================
# Container Images
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Line 35:
#   image: ghcr.io/llm-d/llm-d-cpu:v0.5.0
# =============================================================================
export LLMDBENCH_LLMD_IMAGE_TAG="v0.5.0"
export LLMDBENCH_LLMD_IMAGE_NAME="llm-d-cpu"

# =============================================================================
# SOURCE: gaie-inference-scheduling/values.yaml
# Lines 10-12:
#   name: llm-d-inference-scheduler
#   hub: ghcr.io/llm-d
#   tag: v0.5.0
# =============================================================================
export LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG="v0.5.0"

# =============================================================================
# Decode Stage Configuration
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Line 26:
#   replicas: 2
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 58-63:
#   resources:
#     limits:
#       memory: 64Gi
#       cpu: "64"
#     requests:
#       cpu: "64"
#       memory: 64Gi
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR="64"
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM="64Gi"

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 100-103:
#   - name: dshm
#     emptyDir:
#       medium: Memory
#       sizeLimit: 4Gi
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_SHM_MEM="4Gi"

# =============================================================================
# vLLM Configuration
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 46-47:
#   - "--max_model_len"
#   - "8192"
# =============================================================================
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=8192

# =============================================================================
# Benchmark Framework Convention (not in guide)
# Always use custom model command for llm-d-benchmark deployments
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom

# =============================================================================
# Benchmark Framework Convention (not in guide)
# Preprocess script for environment variable injection
# =============================================================================
export LLMDBENCH_VLLM_COMMON_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 45-47, 49-52:
#   args:
#     - "--disable-hybrid-kv-cache-manager"
#     - "--max_model_len"
#     - "8192"
#   env:
#     - name: VLLM_CPU_NUM_OF_RESERVED_CPU
#       value: "1"
#     - name: VLLM_CPU_KVCACHE_SPACE
#       value: "32"
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << 'EOF' > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--disable-hybrid-kv-cache-manager \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--disable-log-requests
EOF

# =============================================================================
# Environment Variables
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 49-52:
#   env:
#     - name: VLLM_CPU_NUM_OF_RESERVED_CPU
#       value: "1"
#     - name: VLLM_CPU_KVCACHE_SPACE
#       value: "32"
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML=$(mktemp)
cat << 'EOF' > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML
- name: VLLM_CPU_NUM_OF_RESERVED_CPU
  value: "1"
- name: VLLM_CPU_KVCACHE_SPACE
  value: "32"
- name: VLLM_LOGGING_LEVEL
  value: INFO
- name: VLLM_WORKER_MULTIPROC_METHOD
  value: spawn
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
- name: VLLM_SERVER_DEV_MODE
  value: "1"
- name: VLLM_LOAD_FORMAT
  value: auto
EOF

# =============================================================================
# Volume Mounts
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 66-72:
#   volumeMounts:
#     - name: metrics-volume
#       mountPath: /.config
#     - name: torch-compile-cache
#       mountPath: /.cache
#     - name: dshm
#       mountPath: /dev/shm
# Benchmark Framework Convention: Add preprocesses mount
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << 'EOF' > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS
- name: preprocesses
  mountPath: /setup/preprocess
- name: metrics-volume
  mountPath: /.config
- name: torch-compile-cache
  mountPath: /.cache
- name: dshm
  mountPath: /dev/shm
EOF

# =============================================================================
# Volumes
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 95-103:
#   volumes:
#     - name: metrics-volume
#       emptyDir: {}
#     - name: torch-compile-cache
#       emptyDir: {}
#     - name: dshm
#       emptyDir:
#         medium: Memory
#         sizeLimit: 4Gi
# Benchmark Framework Convention: Add preprocesses configMap volume (FIRST)
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=$(mktemp)
cat << 'EOF' > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES
- name: preprocesses
  configMap:
    defaultMode: 0755
    name: llm-d-benchmark-preprocesses
- name: metrics-volume
  emptyDir: {}
- name: torch-compile-cache
  emptyDir: {}
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: 4Gi
EOF

# =============================================================================
# Security Context (CPU-specific)
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 38-43:
#   securityContext:  # NUMA cross-node related
#     seccompProfile:
#       type: Unconfined
#     capabilities:
#       add:
#         - SYS_NICE
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << 'EOF' > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG
{
  "securityContext": {
    "seccompProfile": {
      "type": "Unconfined"
    },
    "capabilities": {
      "add": ["SYS_NICE"]
    }
  }
}
EOF

# =============================================================================
# GAIE Configuration (Gateway API Inference Extension)
# =============================================================================

# =============================================================================
# SOURCE: gaie-inference-scheduling/values.yaml
# Lines 3-6:
#   flags:
#     # in vLLM 10.0+, the metric is renamed while upstream GAIE is still using the old name as default.
#     # See https://github.com/kubernetes-sigs/gateway-api-inference-extension/pull/1905.
#     kv-cache-usage-percentage-metric: "vllm:kv_cache_usage_perc"
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_FLAGS="--kv-cache-usage-percentage-metric=vllm:kv_cache_usage_perc"

# =============================================================================
# Prefill Stage
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Lines 106-107:
#   prefill:
#     create: false
# =============================================================================
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0

# =============================================================================
# Routing Configuration
# =============================================================================

# =============================================================================
# SOURCE: ms-inference-scheduling/values_cpu.yaml
# Line 21:
#   enabled: false  # removes sidecar from deployment - no PD in inference scheduling
# =============================================================================
# Note: Routing proxy disabled in the guide. The llm-d-benchmark framework
# handles routing configuration automatically based on the deployment method.
