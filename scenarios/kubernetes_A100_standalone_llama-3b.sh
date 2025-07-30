# Empty env. variables need to be filled by user

export LLMDBENCH_HF_TOKEN=
export LLMDBENCH_IMAGE_REGISTRY=
export LLMDBENCH_IMAGE_REPO=
export LLMDBENCH_IMAGE_NAME=
export LLMDBENCH_IMAGE_TAG=
export LLMDBENCH_HARNESS_SERVICE_ACCOUNT=llm-d-benchmark-runner

export LLMDBENCH_HARNESS_NAME=nop
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=nop.yaml
export LLMDBENCH_CONTROL_WORK_DIR=~/llm-d-benchmark
export LLMDBENCH_DEPLOY_METHODS=standalone
export LLMDBENCH_DEPLOY_MODEL_LIST=llama-3b


export LLMDBENCH_VLLM_COMMON_NAMESPACE=
export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=
export LLMDBENCH_VLLM_COMMON_REPLICAS=1

export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=safetensors
#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=tensorizer
#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=runai_streamer
#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=fastsafetensors

# set to debug so that all vllm log lines can be categorized
export LLMDBENCH_VLLM_STANDALONE_VLLM_LOGGING_LEVEL=DEBUG

# source preprocessor script that will install libraries for some load formats and set env. variables
# run preprocessor python that will change the debug log date format and pre-serialize a model when using
# tensorizer load format
export LLMDBENCH_VLLM_STANDALONE_PREPROCESS="source /setup/preprocess/standalone-preprocess.sh ; /setup/preprocess/standalone-preprocess.py"

export LLMDBENCH_VLLM_STANDALONE_IMAGE=vllm/vllm-openai:v0.10.0