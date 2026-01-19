# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-vision-3.3-2b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-speech-3.3-8b
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-2b-instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-granite/granite-3.3-8b-instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=ibm-ai-platform/micro-g3.3-8b-instruct-1b
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"

# Deploy methods
#export LLMDBENCH_DEPLOY_METHODS=standalone
#export LLMDBENCH_DEPLOY_METHODS=modelservice

export LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM=0
export LLMDBENCH_VLLM_COMMON_AFFINITY=kubernetes.io/os:linux
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=0
export LLMDBENCH_VLLM_COMMON_REPLICAS=1

export LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY=ghcr.io
export LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO=llm-d
export LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME=llm-d-inference-sim
export LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG=latest

export LLMDBENCH_LLMD_IMAGE_REGISTRY=ghcr.io
export LLMDBENCH_LLMD_IMAGE_REPO=llm-d
export LLMDBENCH_LLMD_IMAGE_NAME=llm-d-inference-sim
export LLMDBENCH_LLMD_IMAGE_TAG=latest

export LLMDBENCH_VLLM_STANDALONE_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_STANDALONE_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_STANDALONE_PREPROCESS; \
/app/llm-d-inference-sim --model REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT \
--block-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN
EOF

# Prefill parameters: 0 prefill pod
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0

# Decode parameters: 2 decode pods
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=0
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=imageDefault
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[]"

# Workload parameters

#export LLMDBENCH_HARNESS_NAME=guidellm
export LLMDBENCH_HARNESS_NAME=inference-perf # (default is "inference-perf")
######export LLMDBENCH_HARNESS_NAME=nop
#export LLMDBENCH_HARNESS_NAME=vllm-benchmark

#export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=sanity_random.yaml # (default is "sanity_random.yaml")
######export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=nop.yaml