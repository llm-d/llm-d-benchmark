# A scenario to capture running inference-sim on a cluster without requiring GPUs
export LLMDBENCH_DEPLOY_METHODS=modelservice
export LLMDBENCH_DEPLOY_MODEL_LIST=inference-sim
export LLMDBENCH_VLLM_COMMON_REPLICAS=1
export LLMDBENCH_LLMD_IMAGE_REGISTRY=ghcr.io
export LLMDBENCH_LLMD_IMAGE_REPO=llm-d
export LLMDBENCH_LLMD_IMAGE_NAME=llm-d-inference-sim
export LLMDBENCH_LLMD_IMAGE_TAG=v0.3.0