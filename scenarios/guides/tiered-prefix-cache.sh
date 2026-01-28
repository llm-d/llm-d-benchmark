# TIERED PREFIX CACHE/PREFIX CACHE OFFLOADING WELL LIT PATH
# Based on https://github.com/llm-d/llm-d/tree/main/guides/tiered-prefix-cache/README.md
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-32B"
#export LLMDBENCH_DEPLOY_MODEL_LIST="RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic"
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-vision-3.3-2b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-speech-3.3-8b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-8b-instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-2b-instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-ai-platform/micro-g3.3-8b-instruct-1b
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="deepseek-ai/DeepSeek-R1-0528"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti

#export LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME=istio

# Routing configuration (via gaie)
#export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="default-plugins.yaml" # already the default

# Routing configuration (via modelservice)
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true # (default is "false")

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU... WILL WORK FOR OpenShift, Kubernetes and GKE)
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200                           # Kubernetes
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-tesla-a100  # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb   # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S                  # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu                                      # ANY GPU (useful for Minikube)

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64
export LLMDBENCH_VLLM_COMMON_CPU_NR=48
export LLMDBENCH_VLLM_COMMON_CPU_MEM=400Gi
export LLMDBENCH_VLLM_COMMON_SHM_MEM=16Gi
export LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM=1

# Uncomment ( ###### ) the following line to enable multi-nic
###### export LLMDBENCH_VLLM_COMMON_PODANNOTATIONS=k8s.v1.cni.cncf.io/networks:multi-nic-compute
# Uncomment ( ######## ) the following to enable automatic detection of network acceleration (roce/gdr or rdma/ib)
######## export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=auto

export LLMDBENCH_VLLM_COMMON_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"

# VLLM_NIXL_SIDE_CHANNEL_HOST is automatically exported
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: PYTHONHASHSEED
  value: "123"
- name: LMCACHE_MAX_LOCAL_CPU_SIZE
  value: "200.0"
- name: UCX_TLS
  value: "sm,cuda_ipc,cuda_copy,tcp"
- name: UCX_SOCKADDR_TLS_PRIORITY
  value: "tcp"
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NIXL_SIDE_CHANNEL_PORT"
- name: VLLM_LOGGING_LEVEL
  value: INFO
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG}
ports:
  - containerPort: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NIXL_SIDE_CHANNEL_PORT
    protocol: TCP
  - containerPort: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT
    name: metrics
    protocol: TCP
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
- name: preprocesses
  mountPath: /setup/preprocess
EOF

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
EOF

# Prefill parameters: 0 prefill pod
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0

# Decode parameters: 2 decode pods
#export LLMDBENCH_LLMD_IMAGE_REGISTRY=docker.io
#export LLMDBENCH_LLMD_IMAGE_REPO=lmcache
#export LLMDBENCH_LLMD_IMAGE_NAME=vllm-openai
#export LLMDBENCH_LLMD_IMAGE_TAG=v0.3.7
#--kv-transfer-config "{\"kv_connector\":\"LMCacheConnectorV1\",\"kv_role\":\"kv_both\"}"

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=$LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=$LLMDBENCH_VLLM_COMMON_CPU_NR
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=$LLMDBENCH_VLLM_COMMON_CPU_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_SHM_MEM=$LLMDBENCH_VLLM_COMMON_SHM_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML=${LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG=${LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=auto # (automatically calculated to be LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM*LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM)
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=$LLMDBENCH_VLLM_COMMON_PODANNOTATIONS
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=$LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--block-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--max-num-seq REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM \
--gpu-memory-utilization REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_MEM_UTIL \
--kv-transfer-config "{\"kv_connector\":\"OffloadingConnector\",\"kv_role\":\"kv_both\",\"kv_connector_extra_config\":{\"num_cpu_blocks\":REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NUM_CPU_BLOCKS}}" \
--enforce-eager \
--disable-log-requests \
--disable-uvicorn-access-log \
--enable_prefix_caching
EOF

# In order to test with default llm-d image, comment all lines with LLMDBENCH_LLLMD_IMAGE_, switch "vllm" to "/opt/venv/bin/vllm" and switch "--kv-transfer-config"

# Workload parameters
export LLMDBENCH_HARNESS_NAME=inference-perf
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=shared_prefix_synthetic.yaml

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/tiered-prefix-cache
