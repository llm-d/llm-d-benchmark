# This is the companion to ocp_H200_deployer_PD.sh, for comparing bare vLLM
# to disaggregated heterogeneous configurations.
#
# All parameters not defined here or exported externally will be the default
# values found in setup/env.sh

export LLMDBENCH_DEPLOY_METHODS=standalone

# Affinity to select node with appropriate GPU
export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3

# Pod parameters
export LLMDBENCH_VLLM_COMMON_CPU_NR=32
export LLMDBENCH_VLLM_COMMON_CPU_MEM=128Gi
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=__tp__
export LLMDBENCH_VLLM_COMMON_REPLICAS=__rep__

# EPP parameters
export LLMDBENCH_EPP_ENABLE_LOAD_AWARE_SCORER=true
export LLMDBENCH_EPP_LOAD_AWARE_SCORER_WEIGHT=1.0

# Timeout for benchmark operations
export LLMDBENCH_CONTROL_WAIT_TIMEOUT=900000
export LLMDBENCH_HARNESS_WAIT_TIMEOUT=900000

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/benchmark_run_sa__suffix__
