# Based on work in progress for llm-d CICD. 
# See https://github.com/llm-d-incubation/llm-d-infra/pull/151/files#diff-c7c92811d800af0f4c7c6cac3bed19b85033bb0c0595a0f8e3f60b5310328bc5
# It's purpose is to drive development of setup/steps/09_deploy_via_modelservice.sh

# Fill in desired values
export LLMDBENCH_HF_TOKEN=
export LLMDBENCH_VLLM_COMMON_NAMESPACE=
export LLMDBENCH_CONTROL_WORK_DIR=

# Temporary for run.sh
export LLMDBENCH_IMAGE_REGISTRY=quay.io
export LLMDBENCH_IMAGE_REPO=namasluk
export LLMDBENCH_IMAGE_NAME=llm-d-benchmark
export LLMDBENCH_IMAGE_TAG=c-ip1

# Cluster specific configuration (fusion6/pokprod001)
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
export LLMDBENCH_VLLM_COMMON_AFFINITY='nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3'

# Model(s)
export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
# export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=800Gi

# modelservice configuration

export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true

export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2
export LLMDBENCH_LLMD_ROUTINGSIDECAR_DEBUG_LEVEL=3

export LLMDBENCH_VLLM_MODELSERVICE_MULTINODE=true

export LLMDBENCH_VLLM_STANDALONE_VLLM_FUSED_MOE_CHUNK_SIZE="1024"
export LLMDBENCH_VLLM_STANDALONE_DP_SIZE_LOCAL="2"
export LLMDBENCH_VLLM_STANDALONE_TRITON_LIBCUDA_PATH="/usr/lib64"
# export LLMDBENCH_VLLM_STANDALONE_HF_HUB_DISABLE_XET="1"
export LLMDBENCH_VLLM_STANDALONE_VLLM_SKIP_P2P_CHECK="1"
export LLMDBENCH_VLLM_STANDALONE_VLLM_RANDOMIZE_DP_DUMMY_INPUTS="1"
export LLMDBENCH_VLLM_STANDALONE_VLLM_USE_DEEP_GEMM="1"
export LLMDBENCH_VLLM_STANDALONE_VLLM_ALL2ALL_BACKEND="deepep_low_latency"
export LLMDBENCH_VLLM_STANDALONE_NVIDIA_GDRCOPY="enabled"
export LLMDBENCH_VLLM_STANDALONE_NVSHMEM_DEBUG="INFO"
export LLMDBENCH_VLLM_STANDALONE_NVSHMEM_REMOTE_TRANSPORT="ibgda"
export LLMDBENCH_VLLM_STANDALONE_NVSHMEM_IB_ENABLE_IBGDA="true"
export LLMDBENCH_VLLM_STANDALONE_NVSHMEM_BOOTSTRAP_UID_SOCK_IFNAME="eth0"
export LLMDBENCH_VLLM_STANDALONE_GLOO_SOCKET_IFNAME="eth0"
export LLMDBENCH_VLLM_STANDALONE_NCCL_SOCKET_IFNAME="eth0"
export LLMDBENCH_VLLM_STANDALONE_NCCL_IB_HCA="ibp"
export LLMDBENCH_VLLM_STANDALONE_VLLM_LOGGING_LEVEL="INFO"
# export LLMDBENCH_VLLM_STANDALONE_HF_HUB_CACHE="/huggingface-cache"
export LLMDBENCH_VLLM_STANDALONE_HF_HUB_CACHE="/model-cache/models"

# export LLMDBENCH_VLLM_MODELSERVICE_MOUNT_MODEL_VOLUME_OVERRIDE=false
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
START_RANK=\$(( \${LWS_WORKER_INDEX:-0} * DP_SIZE_LOCAL ))

        source /opt/vllm/bin/activate
        exec vllm serve \
            /model-cache/models/Qwen/Qwen3-0.6B \
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
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML="LLMDBENCH_VLLM_STANDALONE_VLLM_FUSED_MOE_CHUNK_SIZE,LLMDBENCH_VLLM_STANDALONE_DP_SIZE_LOCAL,LLMDBENCH_VLLM_STANDALONE_TRITON_LIBCUDA_PATH,LLMDBENCH_VLLM_STANDALONE_VLLM_SKIP_P2P_CHECK,LLMDBENCH_VLLM_STANDALONE_VLLM_RANDOMIZE_DP_DUMMY_INPUTS,LLMDBENCH_VLLM_STANDALONE_VLLM_USE_DEEP_GEMM,LLMDBENCH_VLLM_STANDALONE_VLLM_ALL2ALL_BACKEND,LLMDBENCH_VLLM_STANDALONE_NVIDIA_GDRCOPY,LLMDBENCH_VLLM_STANDALONE_NVSHMEM_DEBUG,LLMDBENCH_VLLM_STANDALONE_NVSHMEM_REMOTE_TRANSPORT,LLMDBENCH_VLLM_STANDALONE_NVSHMEM_IB_ENABLE_IBGDA,LLMDBENCH_VLLM_STANDALONE_NVSHMEM_BOOTSTRAP_UID_SOCK_IFNAME,LLMDBENCH_VLLM_STANDALONE_GLOO_SOCKET_IFNAME,LLMDBENCH_VLLM_STANDALONE_NCCL_SOCKET_IFNAME,LLMDBENCH_VLLM_STANDALONE_NCCL_IB_HCA,LLMDBENCH_VLLM_STANDALONE_VLLM_LOGGING_LEVEL,LLMDBENCH_VLLM_STANDALONE_HF_HUB_CACHE"
export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG}
workingDir: /code
imagePullPolicy: Always
# securityContext:
#   runAsUser: 0
#   runAsGroup: 0
#   capabilities:
#     add:
#     - "IPC_LOCK"
#     - "SYS_RAWIO"
EOF
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE="nvidia.com/gpu"
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=2

export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS
START_RANK=\$(( \${LWS_WORKER_INDEX:-0} * DP_SIZE_LOCAL ))

        source /opt/vllm/bin/activate
        exec vllm serve \
            Qwen/Qwen3-0.6B \
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
