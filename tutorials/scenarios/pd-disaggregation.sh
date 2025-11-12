export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=300Mi

# Change this to set the GPU of your interest
export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
# Enable NIXL
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: UCX_TLS
  value: "rc,sm,cuda_ipc,cuda_copy,tcp"
- name: UCX_SOCKADDR_TLS_PRIORITY
  value: "tcp"
###- name: UCX_NET_DEVICES
###  value: mlx5_1:1
###- name: NCCL_IB_HCA
###  value: mlx5_1
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NIXL_SIDE_CHANNEL_PORT"
- name: VLLM_NIXL_SIDE_CHANNEL_HOST
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: VLLM_LOGGING_LEVEL
  value: INFO
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_NR=32
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_MEM=128Gi
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS="[\
--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE____\
--kv-transfer-config____'{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\"}'____\
--disable-log-requests____\
--disable-uvicorn-access-log____\
--no-enable-prefix-caching____\
--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN\
]"

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=32
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=128Gi
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES}
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[\
--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE____\
--kv-transfer-config____'{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\"}'____\
--disable-log-requests____\
--disable-uvicorn-access-log____\
--no-enable-prefix-caching____\
--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN\
]"

# Namespace parameters - TODO modify if needed
export LLMDBENCH_VLLM_COMMON_NAMESPACE=ai-workloads
export LLMDBENCH_HARNESS_NAMESPACE=ai-workloads

# Workload parameters
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=random_concurrent.yaml
export LLMDBENCH_HARNESS_NAME=vllm-benchmark

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/pd-disaggregation