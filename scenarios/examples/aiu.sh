# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
#export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti

export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=ibm.com/aiu_pf
export LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM=2
export LLMDBENCH_VLLM_COMMON_AFFINITY="ibm.com/aiu.product:IBM_Spyre"

export LLMDBENCH_VLLM_COMMON_SHM=64Gi
export LLMDBENCH_VLLM_COMMON_CPU_MEM=200Gi
export LLMDBENCH_VLLM_COMMON_CPU_NR=100
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=10000

export LLMDBENCH_VLLM_COMMON_REPLICAS=1
export LLMDBENCH_VLLM_COMMON_INFERENCE_PORT=3000

export LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT="/cache/models"
export LLMDBENCH_VLLM_COMMON_POD_SCHEDULER="aiu-scheduler"

export LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY=quay.io
export LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO=ibm-aiu
export LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME=vllm-spyre
export LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG=latest.amd64

export LLMDBENCH_VLLM_STANDALONE_ARGS=$(mktemp)

cat << EOF > $LLMDBENCH_VLLM_STANDALONE_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_STANDALONE_PREPROCESS && \
source /etc/profile.d/ibm-aiu-setup.sh && \
source /opt/vllm/bin/activate && \
vllm serve REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM \
--load-format REPLACE_ENV_LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT \
--disable-log-requests \
--model-loader-extra-config "$LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG"
EOF

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
  value: "cuda_ipc,cuda_copy,tcp"
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "5557"
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF
