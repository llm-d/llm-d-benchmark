## Model Server Configuration
# See docs/standup.md
export LLMDBENCH_VLLM_COMMON_NAMESPACE="llmd"
# Available methods are `modelservice` llm-d helmchart, `standalone`
# vLLM deployment, or the name of a SVC.
export LLMDBENCH_DEPLOY_METHODS="modelservice"
# Model to deploy
export LLMDBENCH_DEPLOY_MODEL_LIST="ibm-granite/granite-3.1-2b-instruct"

## Benchmark Configuration
# See docs/run.md
export LLMDBENCH_HARNESS_NAMESPACE="llmdbench"
# The benchmark tool to use
export LLMDBENCH_HARNESS_NAME="guidellm"
# The profile to run the tool with
#export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE="shared_prefix_synthetic"
# Cluster PVC for saving results
export LLMDBENCH_HARNESS_PVC_NAME="workload-pvc"
# Service account to deploy harness with
export LLMDBENCH_HARNESS_SERVICE_ACCOUNT="default"

## Common
# Local Work Directory for saving results
export LLMDBENCH_CONTROL_WORK_DIR="/tmp/modelserve"
# HuggingFace token for model/tokenizer pulling
export LLMDBENCH_HF_TOKEN="llm-d-hf-token" # TODO Must set even if unused
