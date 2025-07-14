# This is the companion to ocp_H200_deployer_PD.sh, for comparing bare vLLM
# to disaggregated heterogeneous configurations.
#
# All parameters not defined here will be the default values found in
# setup/env.sh

export LLMDBENCH_DEPLOY_METHODS=standalone

# Affinity to select node with appropriate GPU
export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200

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
export LLMDBENCH_CONTROL_WAIT_TIMEOUT=5000

# Workload profile selection
#export LLMDBENCH_HARNESS_NAME=fmperf
# 10k/1k ISL/OSL
#export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=pd_disag_10-1_ISL-OSL.yaml
# 10k:100 ISL/OSL
#export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=pd_disag_100-1_ISL-OSL.yaml
export LLMDBENCH_HARNESS_NAME=vllm-benchmark
# 10k/1k ISL/OSL with 1024 concurrent users
# export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=random_1k_concurrent_10-1_ISL-OSL.yaml

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=/files/benchmark_run_sa__suffix__
