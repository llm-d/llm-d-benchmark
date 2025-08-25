# Based on work in progress for llm-d CICD. 
# See https://github.com/llm-d-incubation/llm-d-infra/pull/151/files#diff-c7c92811d800af0f4c7c6cac3bed19b85033bb0c0595a0f8e3f60b5310328bc5
# It's purpose is to drive development of setup/steps/09_deploy_via_modelservice.sh

# Fill in required/desired values
# export LLMDBENCH_HF_TOKEN=
# export LLMDBENCH_VLLM_COMMON_NAMESPACE=
# export LLMDBENCH_CONTROL_WORK_DIR=

# Cluster specific configuration
# export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
export LLMDBENCH_VLLM_COMMON_AFFINITY='nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3'

# Model(s)
export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=20Gi

# Routing configuration (via modelservice) 

export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true

export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2
export LLMDBENCH_LLMD_ROUTINGSIDECAR_DEBUG_LEVEL=3

# Prefill and Decode configiration (via modelservice)

export LLMDBENCH_VLLM_MODELSERVICE_MULTINODE=true

export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: VLLM_FUSED_MOE_CHUNK_SIZE
  value: "1024"
- name: DP_SIZE_LOCAL
  value: "2"
- name: TRITON_LIBCUDA_PATH
  value: "/usr/lib64"
- name: VLLM_SKIP_P2P_CHECK
  value: "1"
- name: VLLM_RANDOMIZE_DP_DUMMY_INPUTS
  value: "1"
- name: VLLM_USE_DEEP_GEMM
  value: "1"
- name: VLLM_ALL2ALL_BACKEND
  value: "deepep_low_latency"
- name: NVIDIA_GDRCOPY
  value: "enabled"
- name: NVSHMEM_DEBUG
  value: "INFO"
- name: NVSHMEM_REMOTE_TRANSPORT
  value: "ibgda"
- name: NVSHMEM_IB_ENABLE_IBGDA
  value: "true"
- name: NVSHMEM_BOOTSTRAP_UID_SOCK_IFNAME
  value: "eth0"
- name: GLOO_SOCKET_IFNAME
  value: "eth0"
- name: NCCL_SOCKET_IFNAME
  value: "eth0"
- name: NCCL_IB_HCA
  value: "ibp"
- name: VLLM_LOGGING_LEVEL
  value: "INFO"
- name: HF_HUB_CACHE
  value: "/model-cache/models"
EOF

# export LLMDBENCH_VLLM_MODELSERVICE_MOUNT_MODEL_VOLUME_OVERRIDE=false
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
START_RANK=\$(( \${LWS_WORKER_INDEX:-0} * DP_SIZE_LOCAL ))
        source /opt/vllm/bin/activate
        exec vllm serve /model-cache/models/Qwen/Qwen3-0.6B \
--port 8200 \
--disable-log-requests \
--disable-uvicorn-access-log \
--enable-expert-parallel \
--data-parallel-hybrid-lb \
--tensor-parallel-size \$TP_SIZE \
--data-parallel-size \$((LWS_GROUP_SIZE * DP_SIZE_LOCAL)) \
--data-parallel-size-local \$DP_SIZE_LOCAL \
--data-parallel-address \${LWS_LEADER_ADDRESS} \
--data-parallel-rpc-port 5555 \
--data-parallel-start-rank \$START_RANK \
--trust-remote-code \
--kv_transfer_config '{"kv_connector":"NixlConnector","kv_role":"kv_both"}'
EOF
export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG}
workingDir: /code
imagePullPolicy: Always
EOF

export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE="nvidia.com/gpu"
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=2

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
EOF

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES}
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: 1Gi
EOF

export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS
START_RANK=\$(( \${LWS_WORKER_INDEX:-0} * DP_SIZE_LOCAL ))

        source /opt/vllm/bin/activate
        exec vllm serve /model-cache/models/Qwen/Qwen3-0.6B \
--port 8000 \
--disable-log-requests \
--disable-uvicorn-access-log \
--enable-expert-parallel \
--data-parallel-hybrid-lb \
--tensor-parallel-size \$TP_SIZE \
--data-parallel-size \$((LWS_GROUP_SIZE * DP_SIZE_LOCAL)) \
--data-parallel-size-local \$DP_SIZE_LOCAL \
--data-parallel-address \${LWS_LEADER_ADDRESS} \
--data-parallel-rpc-port 5555 \
--data-parallel-start-rank \$START_RANK \
--trust-remote-code \
--kv_transfer_config '{"kv_connector":"NixlConnector","kv_role":"kv_both"}'
EOF
