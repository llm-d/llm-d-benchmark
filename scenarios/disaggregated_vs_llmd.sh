# All parameters not defined here or exported externally will be the default
# values found in setup/env.sh

# Affinity to select node with appropriate GPU
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB

# Common parameters across prefill and decode pods
export LLMDBENCH_VLLM_COMMON_CPU_NR=32
export LLMDBENCH_VLLM_COMMON_CPU_MEM=128Gi
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128

# Standalone
export LLMDBENCH_VLLM_COMMON_REPLICAS=1
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=1

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS="[--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR____--disable-log-requests____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE]"

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR____--disable-log-requests____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE]"

# Timeout for benchmark operations
export LLMDBENCH_CONTROL_WAIT_TIMEOUT=900000
export LLMDBENCH_HARNESS_WAIT_TIMEOUT=900000

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/benchmark_run_pd_treatment_nr
