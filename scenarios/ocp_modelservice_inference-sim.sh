# A scenario to capture running inference-sim on a cluster without requiring GPUs
export LLMDBENCH_DEPLOY_METHODS=modelservice
export LLMDBENCH_VLLM_COMMON_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR=0
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=0
export LLMDBENCH_VLLM_COMMON_AFFINITY=kubernetes.io/os:linux
export LLMDBENCH_LLMD_IMAGE_NAME="llm-d-inference-sim"
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=imageDefault
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=imageDefault
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[]"
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS="[]"

# Uncomment the following lines to skip the downloading of a model to a pvc (which is not really used by the simulator anyway)
#export LLMDBENCH_DEPLOY_MODEL_LIST="random/model"
#export LLMDBENCH_HF_TOKEN="llm-d-hf-token"          # <---- TODO: remove this dependency
#export LLMDBENCH_VLLM_MODELSERVICE_URI="hf://random/model"
#export LLMDBENCH_STEP_LIST=0,1,2,7,8,9