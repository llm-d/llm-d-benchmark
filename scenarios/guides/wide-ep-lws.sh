# WIDE EP/DP WITH LWS WELL LIT PATH
# Based on https://github.com/llm-d/llm-d/tree/main/guides/wide-ep-lws
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti

# Routing configuration (via gaie)
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE=pd-config.yaml

# Routing configuration (via modelservice)
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL=true
export LLMDBENCH_VLLM_MODELSERVICE_EPP=true
# export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2 # already the default
export LLMDBENCH_LLMD_ROUTINGSIDECAR_DEBUG_LEVEL=3

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU... WILL WORK FOR OpenShift, Kubernetes and GKE)
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200                           # Kubernetes
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-tesla-a100  # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb   # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S                  # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu                                      # ANY GPU (useful for Minikube)

#             Uncomment to request specific network devices
#####export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/roce_gdr
#######export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/ib
#export LLMDBENCH_VLLM_COMMON_NETWORK_NR=4

#             Uncomment to use hostNetwork (onlye ONE PODE PER NODE)
#export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG=$(mktemp)
#cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG}
#   hostNetwork: true
#   dnsPolicy: ClusterFirstWithHostNet
#EOF

# Prefill and Decode configiration (via modelservice)

export LLMDBENCH_VLLM_MODELSERVICE_MULTINODE=true

# Common parameters across standalone and llm-d (prefill and decode) pods
#export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
#export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64

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
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=64Gi
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
START_RANK=\$(( \${LWS_WORKER_INDEX:-0} * DP_SIZE_LOCAL ))
        source /opt/vllm/bin/activate
        exec vllm serve /model-cache/models/Qwen/Qwen3-0.6B \
--port $LLMDBENCH_VLLM_COMMON_INFERENCE_PORT \
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
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
# Uncomment the following line to enable multi-nic
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=deployed-by:$LLMDBENCH_CONTROL_USERNAME,modelservice:llm-d-benchmark,k8s.v1.cni.cncf.io/networks:multi-nic-compute
# Uncomment the following two lines to enable roce/gdr (or switch to rdma/ib for infiniband)
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=rdma/roce_gdr
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_NR=4
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=64Gi
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

# Workload parameters
export LLMDBENCH_HARNESS_NAME=vllm-benchmark
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=random_concurrent.yaml

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/wide_ep_lws
