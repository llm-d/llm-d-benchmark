# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-vision-3.3-2b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-speech-3.3-8b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-2b-instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-8b-instruct
export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-ai-platform/micro-g3.3-8b-instruct-1b
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"

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

export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=ibm.com/spyre_pf
export LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM=2
export LLMDBENCH_VLLM_COMMON_AFFINITY="ibm.com/spyre.product:IBM_Spyre"

export LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ=32
export LLMDBENCH_VLLM_COMMON_SHM_MEM=64Gi
export LLMDBENCH_VLLM_COMMON_CPU_MEM=750Gi
export LLMDBENCH_VLLM_COMMON_CPU_NR=100
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768

export LLMDBENCH_VLLM_COMMON_REPLICAS=1

export LLMDBENCH_VLLM_COMMON_POD_SCHEDULER=spyre-scheduler

export LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY=icr.io
export LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO=ibmaiu_internal
export LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME=vllm
export LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG=1.0.4-amd64

export LLMDBENCH_LLMD_IMAGE_REGISTRY=icr.io
export LLMDBENCH_LLMD_IMAGE_REPO=ibmaiu_internal
export LLMDBENCH_LLMD_IMAGE_NAME=vllm
export LLMDBENCH_LLMD_IMAGE_TAG=1.0.0-amd64

export LLMDBENCH_VLLM_STANDALONE_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_STANDALONE_ARGS
/home/senuser/container-scripts/simple_vllm_serve.sh REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM \
--block-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE \
--max-num-seqs REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ \
--enable-auto-tool-choice \
--tool-call-parser granite
EOF

export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: SERVED_MODEL_NAME
  value: REPLACE_ENV_LLMDBENCH_DEPLOY_MODEL_LIST
- name: FLEX_COMPUTE
  value: SENTIENT
- name: FLEX_DEVICE
  value: PF
- name: FLEX_HDMA_P2PSIZE
  value: '268435456'
#- name: HF_HUB_DISABLE_XET
#  value: '1'
#- name: HF_HUB_OFFLINE
#  value: '1'
#- name: HF_HUB_CACHE
#  value: /model-storage/
- name: TORCH_SENDNN_CACHE_ENABLE
  value: '1'
- name: TORCH_SENDNN_CACHE_DIR
  value: /mnt/spyre-precompiled-model
- name: VLLM_SPYRE_WARMUP_BATCH_SIZES
  value: '1,4'
- name: VLLM_SPYRE_WARMUP_PROMPT_LENS
  value: '4096,1024'
- name: VLLM_SPYRE_WARMUP_NEW_TOKENS
  value: '1024,256'
- name: DTCOMPILER_KEEP_EXPORT
  value: 'true'
- name: PORT
  value: "REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT"
- name: DTCOMPILER_KEEP_EXPORT
  value: 'true'
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS
- name: spyre-precompiled-model
  mountPath: /mnt/spyre-precompiled-model
EOF
export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES
- name: spyre-precompiled-model
  persistentVolumeClaim:
    claimName: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_EXTRA_PVC_NAME
EOF

# Prefill parameters: 0 prefill pod
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_RESOURCE=ibm.com/spyre_pf
# export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR=0

# Decode parameters: 2 decode pods
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=64Gi
#              Uncomment (###) the following line to enable multi-nic
###export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=k8s.v1.cni.cncf.io/networks:multi-nic-compute
#              Uncomment (#####) the following two lines to enable roce/gdr (or switch to rdma/ib for infiniband)
#####export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=rdma/roce_gdr
#####export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_NR=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
/home/senuser/container-scripts/simple_vllm_serve.sh /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL  \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM \
--max-num-seqs REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ \
--enable-auto-tool-choice \
--tool-call-parser granite
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_RESOURCE=ibm.com/spyre_pf
# export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=1

# Workload parameters

#export LLMDBENCH_HARNESS_NAME=guidellm
export LLMDBENCH_HARNESS_NAME=inference-perf # (default is "inference-perf")
######export LLMDBENCH_HARNESS_NAME=nop
#export LLMDBENCH_HARNESS_NAME=vllm-benchmark

#export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=sanity_random.yaml # (default is "sanity_random.yaml")
######export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=nop.yaml
