# WIDE EP/DP WITH LWS WELL LIT PATH
# Based on https://github.com/llm-d/llm-d/tree/main/guides/wide-ep-lws/README.md
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS|LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES|LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-32B"
#export LLMDBENCH_DEPLOY_MODEL_LIST="RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic"
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-vision-3.3-2b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-speech-3.3-8b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-8b-instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-2b-instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-ai-platform/micro-g3.3-8b-instruct-1b
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"
export LLMDBENCH_DEPLOY_MODEL_LIST="deepseek-ai/DeepSeek-R1-0528"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=2Ti

#export LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME=istio

# Routing configuration (via gaie)
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="custom-plugins.yaml"
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS
custom-plugins.yaml: |
  apiVersion: inference.networking.x-k8s.io/v1alpha1
  kind: EndpointPickerConfig
  plugins:
  - type: prefill-header-handler
  - type: prefill-filter
  - type: decode-filter
  - type: random-picker
    parameters:
      maxNumOfEndpoints: 1
  - type: pd-profile-handler
    parameters:
      threshold: 0
      hashBlockSize: 5
  schedulingProfiles:
  - name: prefill
    plugins:
    - pluginRef: prefill-filter
    - pluginRef: random-picker
  - name: decode
    plugins:
    - pluginRef: decode-filter
    - pluginRef: random-picker
EOF
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL_PROVIDER_CONFIG=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL_PROVIDER_CONFIG
destinationRule:
  host: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL-gaie-epp
  trafficPolicy:
    connectionPool:
      http:
        http1MaxPendingRequests: 256000
        maxRequestsPerConnection: 256000
        http2MaxRequests: 256000
        idleTimeout: "900s"
      tcp:
        maxConnections: 256000
        maxConnectionDuration: "1800s"
        connectTimeout: "900s"
EOF

# Routing configuration (via modelservice)
# export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2 # already the default

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU... WILL WORK FOR OpenShift, Kubernetes and GKE)
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200                           # Kubernetes
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-tesla-a100  # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb   # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S                  # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu                                      # ANY GPU (useful for Minikube)

export LLMDBENCH_VLLM_COMMON_POD_SCHEDULER=custom-binpack-scheduler

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64
export LLMDBENCH_VLLM_COMMON_CPU_NR=32
export LLMDBENCH_VLLM_COMMON_CPU_MEM=512Gi
export LLMDBENCH_VLLM_COMMON_SHM_MEM=32Gi
export LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM=1
export LLMDBENCH_VLLM_COMMON_DATA_LOCAL_PARALLELISM=8
export LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM=2
export LLMDBENCH_VLLM_COMMON_EPHEMERAL_STORAGE=1Ti
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL=0.75

# Uncomment ( ###### ) the following line to enable multi-nic
###### export LLMDBENCH_VLLM_COMMON_PODANNOTATIONS=k8s.v1.cni.cncf.io/networks:multi-nic-compute
export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=auto

export LLMDBENCH_VLLM_COMMON_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"

# VLLM_NIXL_SIDE_CHANNEL_HOST is automatically exported
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: TRITON_LIBCUDA_PATH
  value: /usr/lib64
- name: VLLM_SKIP_P2P_CHECK
  value: "1"
- name: VLLM_RANDOMIZE_DP_DUMMY_INPUTS
  value: "1"
- name: VLLM_USE_DEEP_GEMM
  value: "1"
- name: VLLM_ALL2ALL_BACKEND
  value: deepep_high_throughput
- name: NVIDIA_GDRCOPY
  value: enabled
- name: NVSHMEM_REMOTE_TRANSPORT
  value: ibgda
- name: NVSHMEM_IB_ENABLE_IBGDA
  value: "true"
- name: NVSHMEM_BOOTSTRAP_UID_SOCK_IFNAME
  value: eth0
- name: GLOO_SOCKET_IFNAME
  value: eth0
- name: NCCL_SOCKET_IFNAME
  value: eth0
- name: VLLM_LOGGING_LEVEL
  value: INFO
- name: CUDA_CACHE_PATH
  value: /var/cache/vllm/cuda
- name: CCACHE_DIR
  value: /var/cache/vllm/ccache
- name: VLLM_CACHE_ROOT
  value: /var/cache/vllm/vllm
- name: FLASHINFER_WORKSPACE_BASE
  value: /var/cache/vllm/flashinfer
- name: HF_HUB_CACHE
  value: /var/cache/huggingface
- name: HF_HUB_DISABLE_XET
  value: "1"
- name: NCCL_IB_HCA
  value: ibp
- name: NVSHMEM_HCA_PREFIX
  value: ibp
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
- name: preprocesses
  mountPath: /setup/preprocess
#- name: hf-cache
#  mountPath: /var/cache/huggingface
#- name: jit-cache
#  mountPath: /var/cache/vllm
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES}
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM # roughly 32MB per local DP plus scratch space
- name: preprocesses
  configMap:
    defaultMode: 0755
    name: llm-d-benchmark-preprocesses
#- hostPath:
#    path: /mnt/local/hf-cache
#    type: DirectoryOrCreate
#  name: hf-cache
#- hostPath:
#    path: /mnt/local/jit-cache
#    type: DirectoryOrCreate
#  name: jit-cache
EOF

#export LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG=$(mktemp)
#cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG}
#securityContext:
#  capabilities:
#    add:
#    - IPC_LOCK
#    - SYS_RAWIO
#  runAsGroup: 0
#  runAsUser: 0
#imagePullPolicy: Always
#EOF

#             Uncomment to use hostNetwork (onlye ONE PODE PER NODE)
#export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG=$(mktemp)
#cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG}
#   hostNetwork: true
#   dnsPolicy: ClusterFirstWithHostNet
#EOF

# Prefill and Decode configiration (via modelservice)

export LLMDBENCH_VLLM_MODELSERVICE_MULTINODE=true

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_MODELSERVICE_MOUNT_MODEL_VOLUME_OVERRIDE=true
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM=$LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_LOCAL_PARALLELISM=$LLMDBENCH_VLLM_COMMON_DATA_LOCAL_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM=$LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NUM_WORKERS_PARALLELISM=$LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_NR=$LLMDBENCH_VLLM_COMMON_CPU_NR
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_MEM=LLMDBENCH_VLLM_COMMON_CPU_MEM
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_SHM_MEM=$LLMDBENCH_VLLM_COMMON_SHM_MEM
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ENVVARS_TO_YAML=${LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML}
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUME_MOUNTS=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUMES=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES}
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_CONTAINER_CONFIG=${LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG}
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_MEM_UTIL=$LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EPHEMERAL_STORAGE=$LLMDBENCH_VLLM_COMMON_EPHEMERAL_STORAGE
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR=auto # (automatically calculated to be LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM*LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM)
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS=$LLMDBENCH_VLLM_COMMON_PODANNOTATIONS
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NETWORK_RESOURCE=$LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PREPROCESS; \
exec vllm serve \
  REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
  --served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
  --port REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_INFERENCE_PORT \
  --trust-remote-code \
  --disable-uvicorn-access-log \
  --data-parallel-hybrid-lb \
  --enable-expert-parallel \
  --tensor-parallel-size \$TP_SIZE \
  --data-parallel-size \$((LWS_GROUP_SIZE * DP_SIZE_LOCAL)) \
  --data-parallel-size-local \$DP_SIZE_LOCAL \
  --data-parallel-address \${LWS_LEADER_ADDRESS} \
  --data-parallel-rpc-port 5555 \
  --data-parallel-start-rank \$START_RANK \
  --kv_transfer_config '{"kv_connector":"NixlConnector",
                          "kv_role":"kv_both",
                          "kv_load_failure_policy":"fail"}' \
  --async-scheduling \
  --enable-dbo \
  --dbo-prefill-token-threshold 32 \
  --enable-eplb \
  --eplb-config '{"window_size":"1000",
                  "step_interval":"3000",
                  "num_redundant_experts":"32",
                  "log_balancedness":"False"}' \
  --gpu-memory-utilization REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_MEM_UTIL
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM=$LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM=$LLMDBENCH_VLLM_COMMON_DATA_LOCAL_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=$LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NUM_WORKERS_PARALLELISM=$LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=$LLMDBENCH_VLLM_COMMON_CPU_NR
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=$LLMDBENCH_VLLM_COMMON_CPU_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_SHM_MEM=$LLMDBENCH_VLLM_COMMON_SHM_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML=${LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG=${LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_MEM_UTIL=$LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EPHEMERAL_STORAGE=$LLMDBENCH_VLLM_COMMON_EPHEMERAL_STORAGE
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=auto # (automatically calculated to be LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM*LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM)
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=$LLMDBENCH_VLLM_COMMON_PODANNOTATIONS
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=$LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
exec vllm serve \
  REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
  --served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
  --port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
  --trust-remote-code \
  --disable-uvicorn-access-log \
  --data-parallel-hybrid-lb \
  --enable-expert-parallel \
  --tensor-parallel-size \$TP_SIZE \
  --data-parallel-size \$((LWS_GROUP_SIZE * DP_SIZE_LOCAL)) \
  --data-parallel-size-local \$DP_SIZE_LOCAL \
  --data-parallel-address \${LWS_LEADER_ADDRESS} \
  --data-parallel-rpc-port 5555 \
  --data-parallel-start-rank \$START_RANK \
  --kv_transfer_config '{"kv_connector":"NixlConnector",
                          "kv_role":"kv_both",
                          "kv_load_failure_policy":"fail"}' \
  --async-scheduling \
  --enable-dbo \
  --dbo-decode-token-threshold 32 \
  --enable-eplb \
  --eplb-config '{"window_size":"1000",
                  "step_interval":"3000",
                  "num_redundant_experts":"32",
                  "log_balancedness":"False"}' \
  --compilation_config '{"cudagraph_mode": "FULL_DECODE_ONLY"}' \
  --kv-cache-memory-bytes=${KV_CACHE_MEMORY_BYTES-}
EOF

# Workload parameters
export LLMDBENCH_HARNESS_NAME=vllm-benchmark
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=random_concurrent.yaml

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/wide_ep_lws
