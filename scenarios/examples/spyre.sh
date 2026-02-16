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
export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-ai-platform/micro-g3.3-8b-instruct-1b
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="deepseek-ai/DeepSeek-R1-0528"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
#export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti
export LLMDBENCH_VLLM_COMMON_EXTRA_PVC_NAME=spyre-precompiled-model

# Deploy methods
#export LLMDBENCH_DEPLOY_METHODS=standalone
#export LLMDBENCH_DEPLOY_METHODS=modelservice

#export LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME=istio

export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=ibm.com/spyre_vf
export LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM=4
export LLMDBENCH_VLLM_COMMON_AFFINITY="ibm.com/spyre.product:IBM_Spyre"
export LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS=1024
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768
export LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ=32
export LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS=1024
export LLMDBENCH_VLLM_COMMON_CPU_NR=100
export LLMDBENCH_VLLM_COMMON_CPU_MEM=750Gi
export LLMDBENCH_VLLM_COMMON_SHM_MEM=64Gi

export LLMDBENCH_VLLM_COMMON_REPLICAS=1

export LLMDBENCH_VLLM_COMMON_POD_SCHEDULER=spyre-scheduler

export LLMDBENCH_VLLM_COMMON_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"

export LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY=us.icr.io
export LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO=wxpe-cicd-internal/amd64
export LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME=aiu-vllm
export LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG=v1.1.1-rc.3-amd64

export LLMDBENCH_LLMD_IMAGE_REGISTRY=us.icr.io
export LLMDBENCH_LLMD_IMAGE_REPO=wxpe-cicd-internal/amd64
export LLMDBENCH_LLMD_IMAGE_NAME=aiu-vllm
export LLMDBENCH_LLMD_IMAGE_TAG=v1.1.1-rc.3-amd64

export LLMDBENCH_VLLM_STANDALONE_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_STANDALONE_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_COMMON_PREPROCESS; \
/home/senuser/container-scripts/simple_vllm_serve.sh REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port \$VLLM_INFERENCE_PORT \
--max-model-len \$VLLM_MAX_MODEL_LEN \
--max-num-seq \$VLLM_MAX_NUM_SEQ \
--tensor-parallel-size \$VLLM_TENSOR_PARALLELISM \
--enable-auto-tool-choice \
--tool-call-parser granite \
--max-num-batched-tokens \$VLLM_MAX_NUM_BATCHED_TOKENS \
--enable-prefix-caching
EOF

export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: SERVED_MODEL_NAME
  value: REPLACE_ENV_LLMDBENCH_DEPLOY_MODEL_LIST
- name: FLEX_COMPUTE
  value: SENTIENT
- name: FLEX_DEVICE
  value: VF
- name: FLEX_HDMA_P2PSIZE
  value: '268435456'
- name: FLEX_HDMA_COLLSIZE
  value: '268435456'
- name: HF_HUB_DISABLE_XET
  value: '1'
- name: TORCH_SENDNN_CACHE_ENABLE
  value: '1'
- name: TORCH_SENDNN_CACHE_DIR
  value: /mnt/spyre-precompiled-model
- name: PORT
  value: "REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT"
- name: VLLM_SPYRE_DYNAMO_BACKEND
  value: 'sendnn'
- name: VLLM_SPYRE_USE_CB
  value: '1'
- name: VLLM_SPYRE_USE_CHUNKED_PREFILL
  value: '1'
- name: VLLM_DT_CHUNK_LEN
  value: '1024'
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS
- name: spyre-precompiled-model
  mountPath: /mnt/spyre-precompiled-model
- name: dshm
  mountPath: /dev/shm
- name: preprocesses
  mountPath: /setup/preprocess
EOF
export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES
- name: spyre-precompiled-model
  persistentVolumeClaim:
    claimName: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_EXTRA_PVC_NAME
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM # roughly 32MB per local DP plus scratch space
- name: preprocesses
  configMap:
    defaultMode: 0755
    name: llm-d-benchmark-preprocesses
EOF

export LLMDBENCH_VLLM_STANDALONE_ENVVARS_TO_YAML=$LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
export LLMDBENCH_VLLM_STANDALONE_EXTRA_VOLUME_MOUNTS=$LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS
export LLMDBENCH_VLLM_STANDALONE_EXTRA_VOLUMES=$LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES

# Prefill parameters: 0 prefill pod
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_RESOURCE=$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE

# Decode parameters: 2 decode pods
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=${LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_RESOURCE=$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=$LLMDBENCH_VLLM_COMMON_CPU_NR
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=$LLMDBENCH_VLLM_COMMON_CPU_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_SHM_MEM=$LLMDBENCH_VLLM_COMMON_SHM_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML=$LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=$LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=$LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
/home/senuser/container-scripts/simple_vllm_serve.sh /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL  \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM \
--max-num-seqs REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ \
--enable-auto-tool-choice \
--tool-call-parser granite \
--max-num-batched-tokens REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS \
--enable-prefix-caching
EOF

# Workload parameters

#export LLMDBENCH_HARNESS_NAME=guidellm
export LLMDBENCH_HARNESS_NAME=inference-perf # (default is "inference-perf")
# export LLMDBENCH_HARNESS_NAME=vllm-benchmark # (default is "inference-perf")
######export LLMDBENCH_HARNESS_NAME=nop
#export LLMDBENCH_HARNESS_NAME=vllm-benchmark
export LLMDBENCH_HARNESS_WAIT_TIMEOUT=36000

export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=sanity_random.yaml # (default is "sanity_random.yaml")
# export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=fixed_dataset.yaml # (default is "sanity_random.yaml")
######export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=nop.yaml
