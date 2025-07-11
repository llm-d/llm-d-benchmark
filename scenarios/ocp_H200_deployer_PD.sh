# Tracking benchmark from https://github.com/llm-d/llm-d-deployer/pull/368/files
#
# All parameters not defined here will be the default values found in
# setup/env.sh

# Affinity to select node with appropriate GPU
export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200

# Pick a model
export LLMDBENCH_DEPLOY_MODEL_LIST=RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic
#export LLMDBENCH_DEPLOY_MODEL_LIST=meta-llama/Llama-3.3-70B-Instruct
#export LLMDBENCH_DEPLOY_MODEL_LIST=Qwen/Qwen1.5-MoE-A2.7B-Chat

# Common parameters across prefill and decode pods
export LLMDBENCH_VLLM_COMMON_CPU_NR=32
export LLMDBENCH_VLLM_COMMON_CPU_MEM=128Gi
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128

# Prefill parameters
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS=4
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR=1
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_EXTRA_ARGS="[--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR____--disable-log-requests____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE]"

# Decode parameters
export LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_NR=4
export LLMDBENCH_VLLM_DEPLOYER_DECODE_EXTRA_ARGS="[--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_NR____--disable-log-requests____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE]"

# EPP parameters
export LLMDBENCH_VLLM_DEPLOYER_EPP_PD_ENABLED=true
export LLMDBENCH_VLLM_DEPLOYER_EPP_PD_PROMPT_LEN_THRESHOLD=1
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_LOAD_AWARE_SCORER=true
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_LOAD_AWARE_SCORER=true

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
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=random_1k_concurrent_10-1_ISL-OSL.yaml

# llm-d-deployer preset
export LLMDBENCH_VLLM_DEPLOYER_BASECONFIGMAPREFNAME=basic-gpu-with-nixl-preset

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=/files/benchmark_run_pd