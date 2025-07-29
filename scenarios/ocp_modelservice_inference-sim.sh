# A scenario to capture running inference-sim on a cluster without requiring GPUs
export LLMDBENCH_DEPLOY_METHODS=modelservice
export LLMDBENCH_DEPLOY_MODEL_LIST="random/model"
export LLMDBENCH_VLLM_COMMON_REPLICAS=1
export LLMDBENCH_LLMD_IMAGE_REGISTRY="ghcr.io"
export LLMDBENCH_LLMD_IMAGE_REPO="llm-d"
export LLMDBENCH_LLMD_IMAGE_NAME="llm-d-inference-sim"
export LLMDBENCH_LLMD_IMAGE_TAG="v0.3.0"
export LLMDBENCH_HF_TOKEN="llm-d-hf-token"          # <---- TODO: remove this dependency
export LLMDBENCH_VLLM_MODELSERVICE_URI="hf://random/model"
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=imageDefault
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=imageDefault
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[]"
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS="[]"
export LLMDBENCH_CLIOVERRIDE_STEP_LIST=[0,1,2,7,8,9]

# TODO:
# Remove defining acceleratorTypes and resources for this scenario