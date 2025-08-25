# All parameters not defined here or exported externally will be the default
# values found in setup/env.sh

export LLMDBENCH_DEPLOY_MODEL_LIST=meta-llama/Llama-3.1-70B-Instruct

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=shared_prefix_synthetic.yaml
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti
export LLMDBENCH_VLLM_COMMON_CPU_NR=16
export LLMDBENCH_VLLM_COMMON_CPU_MEM=64Gi
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: PYTHONHASHSEED
  value: "42"
- name: POD_IP
  valueFrom:
    fieldRef:
      apiVersion: v1
      fieldPath: status.podIP
- name: UCX_TLS
  value: "cuda_ipc,cuda_copy,tcp"
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "5557"
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=4
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=4
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT=8200
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT \
--block-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--prefix-caching-hash-algo sha256_cbor_64bit \
--kv-transfer-config '{"kv_connector":"NixlConnector", "kv_role":"kv_both"}' \
--kv-events-config "{\"enable_kv_cache_events\":true,\"publisher\":\"zmq\",\"endpoint\":\"tcp://gaie-REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_RELEASE.REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NAMESPACE.svc.cluster.local:5557\",\"topic\":\"kv@\${POD_IP}@QREPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL\"}" \
--enforce-eager
EOF

# GAIE parameters
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS=default

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/precise_prefix_cache_aware
