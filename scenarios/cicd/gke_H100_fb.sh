export LLMDBENCH_CONTROL_WORK_DIR=/tmp/cicd/
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.2-1B"
export LLMDBENCH_VLLM_COMMON_NAMESPACE=llmdbenchcicd
export LLMDBENCH_HARNESS_NAMESPACE=llmdbenchcicd
export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti
export LLMDBENCH_VLLM_MODELSERVICE_RELEASE=llmdbenchcicd
export LLMDBENCH_VLLM_COMMON_REPLICAS=1
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=1
export LLMDBENCH_HARNESS_NAME=inference-perf
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=sanity_random.yaml

export LLMDBENCH_VLLM_STANDALONE_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_STANDALONE_ARGS
export LD_LIBRARY_PATH=\${LD_LIBRARY_PATH}:/usr/local/nvidia/lib64; \
vllm serve REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port \$VLLM_INFERENCE_PORT \
--block-size \$VLLM_BLOCK_SIZE \
--max-model-len \$VLLM_MAX_MODEL_LEN \
--max-num-seq \$VLLM_MAX_NUM_SEQ \
--load-format \$VLLM_LOAD_FORMAT \
--gpu-memory-utilization \$VLLM_ACCELERATOR_MEM_UTIL \
--max-num-seqs \$VLLM_MAX_NUM_SEQ \
--tensor-parallel-size \$VLLM_TENSOR_PARALLELISM \
--disable-log-requests \
--disable-uvicorn-access-log \
--no-enable-prefix-caching
EOF

export LLMDBENCH_VLLM_MONITORING_PODMONITOR_ENABLED=false
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_MONITORING_PROMETHEUS_ENABLED=false

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
export LD_LIBRARY_PATH=\${LD_LIBRARY_PATH}:/usr/local/cuda-12.9/compat; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port \$VLLM_METRICS_PORT \
--block-size \$VLLM_BLOCK_SIZE \
--max-model-len \$VLLM_MAX_MODEL_LEN \
--tensor-parallel-size \$VLLM_TENSOR_PARALLELISM \
--gpu-memory-utilization \$VLLM_ACCELERATOR_MEM_UTIL \
--no-enable-log-requests \
--disable-uvicorn-access-log \
--no-enable-prefix-caching
EOF

export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS
export LD_LIBRARY_PATH=\${LD_LIBRARY_PATH}:/usr/local/cuda-12.9/compat; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port \$VLLM_INFERENCE_PORT \
--block-size \$VLLM_BLOCK_SIZE \
--max-model-len \$VLLM_MAX_MODEL_LEN \
--tensor-parallel-size \$VLLM_TENSOR_PARALLELISM \
--gpu-memory-utilization \$VLLM_ACCELERATOR_MEM_UTIL \
--no-enable-log-requests \
--disable-uvicorn-access-log \
--no-enable-prefix-caching
EOF
