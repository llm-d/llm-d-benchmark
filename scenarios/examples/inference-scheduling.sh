# INFERENCE SCHEDULING WELL LIT PATH

# Model parameters
export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=300Gi

# Workload parameters
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=shared_prefix_synthetic.yaml
export LLMDBENCH_HARNESS_NAME=inference-perf

# Routing configuration (via gaie)
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true
export LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG=v0.2.1

# Hardware config
export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3

export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: UCX_TLS
  value: "cuda_ipc,cuda_copy,tcp"
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "5557"
- name: VLLM_NIXL_SIDE_CHANNEL_HOST
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG}
ports:
  - containerPort: 5557
    protocol: TCP
  - containerPort: 8200
    name: metrics
    protocol: TCP
EOF

# Shared vLLM configs
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64

# Prefill parameters: 0 prefill pod
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR=0

# Decode parameters: 2 decode pods
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=64Gi
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT=8200
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[\
--enforce-eager____\
--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE____\
--kv-transfer-config____'{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\"}'____\
--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM____\
--disable-log-requests____\
--disable-uvicorn-access-log____\
--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN\
]"
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=1


# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/inference-scheduling

# Use the following image tag (ghcr.io/llm-d/llm-d-benchmark) to obtain the latest benchmark report format as of 9/25/25
export LLMDBENCH_IMAGE_TAG="v0.3.0rc2"
