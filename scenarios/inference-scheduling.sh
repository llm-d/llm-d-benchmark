# Fill in desired values
# export LLMDBENCH_HF_TOKEN=
# export LLMDBENCH_VLLM_COMMON_NAMESPACE=
# export LLMDBENCH_CONTROL_WORK_DIR=

# INFERENCE SCHEDULING WELL LIT PATH
# Based on https://github.com/llm-d-incubation/llm-d-infra/tree/main/quickstart/examples/inference-scheduling
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Cluster specific configuration
# export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
# export LLMDBENCH_VLLM_COMMON_AFFINITY='nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3'

# Model(s)
# export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
export LLMDBENCH_DEPLOY_MODEL_LIST=meta-llama/Llama-3.1-8B-Instruct
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=20Gi

# Routing configuration (via gaie)
LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="plugins-v2.yaml"

# Routing configuration (via modelservice) 
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true
export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2

# Prefill and Decode configiration (via modelservice)

# export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE="nvidia.com/gpu"

export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: CUDA_VISIBLE_DEVICES
  value: "0"
- name: UCX_TLS
  value: "cuda_ipc,cuda_copy,tcp"
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "5557"
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
EOF

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG}
ports:
  - containerPort: 5557
    protocol: TCP
  - containerPort: 8200
    name: metrics
    protocol: TCP
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=["--enforce-eager____--kv-transfer-config____{\\\"kv_connector\\\":\\\"NixlConnector\\\",\\\"kv_role\\\":\\\"kv_both\\\"}"]

export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS=["--enforce-eager____--kv-transfer-config____{\\\"kv_connector\\\":\\\"NixlConnector\\\",\\\"kv_role\\\":\\\"kv_both\\\"}"]
