export LLMDBENCH_DEPLOY_MODEL_LIST=llama-70b
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=8
export LLMDBENCH_VLLM_COMMON_CPU_NR=16
export LLMDBENCH_VLLM_COMMON_CPU_MEM=64Gi
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=250000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128
export LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS=32768
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
- |
        vllm serve REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--port 8200 \
--block-size 64 \
--prefix-caching-hash-algo sha256_cbor_64bit \
--enforce-eager \
--kv-transfer-config '{"kv_connector":"NixlConnector", "kv_role":"kv_both"}' \
--kv-events-config "{\"enable_kv_cache_events\":true,\"publisher\":\"zmq\",\"endpoint\":\"tcp://gaie-kv-events-epp.llm-d.svc.cluster.local:5557\",\"topic\":\"kv@${POD_IP}@Qwen/Qwen3-0.6B\"}"
EOF

export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
      - name: PYTHONHASHSEED
        value: "42"
      - name: POD_IP
        valueFrom:
          fieldRef:
            apiVersion: v1
            fieldPath: status.podIP
      - name: CUDA_VISIBLE_DEVICES
        value: "0"
      - name: UCX_TLS
        value: "cuda_ipc,cuda_copy,tcp"
      - name: VLLM_NIXL_SIDE_CHANNEL_PORT
        value: "5557"
      - name: VLLM_LOGGING_LEVEL
        value: DEBUG
EOF
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0