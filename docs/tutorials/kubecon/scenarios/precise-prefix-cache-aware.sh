# PRECISE PREFIX CACHE AWARE ROUTING
# KubeCon 2025 

# Model parameters
export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-32B"

# PVC parameters
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti

export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb 

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64

#             Uncomment (###) to select additional network devices (e.g., when multi-nic is enabled)
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: PYTHONHASHSEED
  value: "42"
- name: POD_IP
  valueFrom:
    fieldRef:
      apiVersion: v1
      fieldPath: status.podIP
- name: UCX_TLS
  value: "rc,sm,cuda_ipc,cuda_copy,tcp"
- name: UCX_SOCKADDR_TLS_PRIORITY
  value: "tcp"
###- name: UCX_NET_DEVICES
###  value: mlx5_1:1
###- name: NCCL_IB_HCA
###  value: mlx5_1
- name: VLLM_NIXL_SIDE_CHANNEL_HOST
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "5557"
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG}
ports:
  - containerPort: ${LLMDBENCH_VLLM_COMMON_NIXL_SIDE_CHANNEL_PORT}
    protocol: TCP
  - containerPort: ${LLMDBENCH_VLLM_COMMON_METRICS_PORT}
    name: metrics
    protocol: TCP
EOF

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=64Gi
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=4
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--block-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--prefix-caching-hash-algo sha256_cbor \
--kv-transfer-config '{"kv_connector":"NixlConnector", "kv_role":"kv_both"}' \
--kv-events-config "{\"enable_kv_cache_events\":true,\"publisher\":\"zmq\",\"endpoint\":\"tcp://REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_SERVICE_NAME.REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NAMESPACE.svc.cluster.local:5557\",\"topic\":\"kv@\${POD_IP}@QREPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL\"}" \
--enforce-eager \
--tensor-parallel-size 2
EOF

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

# Workload parameters
export LLMDBENCH_HARNESS_NAME=inference-perf
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=shared_prefix_synthetic_large.yaml

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/precise_prefix_cache_aware
export LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME=gke
