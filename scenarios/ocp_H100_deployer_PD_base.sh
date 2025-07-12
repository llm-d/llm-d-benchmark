# Tracking benchmark from https://github.com/llm-d/llm-d-deployer/pull/368/files
#
# All parameters not defined here will be the default values found in
# setup/env.sh

# Custom image for llm-d-benchmark
export LLMDBENCH_IMAGE_REGISTRY=quay.io
export LLMDBENCH_IMAGE_REPO=namasluk/llm-d-benchmark
export LLMDBENCH_IMAGE_TAG=0.1.11

# Custom image for llm-d
export LLMDBENCH_LLMD_IMAGE_REGISTRY=docker.io
export LLMDBENCH_LLMD_IMAGE_REPO=robertgouldshaw2/vllm-nixl
export LLMDBENCH_LLMD_IMAGE_TAG=nixl-oh-debug-fixed-0.3

# Affinity to select node with appropriate GPU
export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3

# Common parameters across prefill and decode pods
export LLMDBENCH_VLLM_COMMON_CPU_NR=32
export LLMDBENCH_VLLM_COMMON_CPU_MEM=128Gi
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128

# Prefill parameters
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS=__p_rep__
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR=__p_tp__
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_EXTRA_ARGS="[--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR____--disable-log-requests____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE]"

# Decode parameters
export LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS=__d_rep__
export LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_NR=__d_tp__
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
#export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=random_1k_concurrent_10-1_ISL-OSL.yaml

# llm-d-deployer preset
export LLMDBENCH_VLLM_DEPLOYER_BASECONFIGMAPREFNAME=basic-gpu-with-nixl-preset

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=/files/benchmark_run_pd__suffix__
