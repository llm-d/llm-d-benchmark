# INFERENCE SCHEDULING WELL LIT PATH
# Based on https://github.com/llm-d-incubation/llm-d-infra/tree/main/quickstart/examples/inference-scheduling
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
# export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
# export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti


# Workload parameters
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=shared_prefix_synthetic.yaml
export LLMDBENCH_HARNESS_NAME=inference-perf

# Routing configuration (via gaie)
#export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="plugins-v2.yaml" (default is default-plugins.yaml)
export LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG=v0.2.1

# Routing configuration (via modelservice)
# export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=false # already the default
# export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2 # already the default

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU)
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB

#             Uncomment to request specific network devices
#export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/roce_gdr
#export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/ib
#export LLMDBENCH_VLLM_COMMON_NETWORK_NR=4

#             Uncomment to use hostNetwork (only ONE PODE PER NODE)
#export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG=$(mktemp)
#cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG}
#   hostNetwork: true
#   dnsPolicy: ClusterFirstWithHostNet
#EOF

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
EOF

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES}
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: 16Gi
EOF

export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: UCX_TLS
  value: "cuda_ipc,cuda_copy,tcp"
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "5557"
- name: VLLM_NIXL_SIDE_CHANNEL_HOST
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
- name: NCCL_DEBUG
  value: INFO
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
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

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=4
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=64Gi
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT=8200
# Uncomment the following line to enable multi-nic
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=deployed-by:$(id -un),modelservice:llm-d-benchmark,k8s.v1.cni.cncf.io/networks:multi-nic-compute
# Uncomment the following two lines to enable roce/gdr (or switch to rdma/ib for infiniband)
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=rdma/roce_gdr
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_NR=4
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[\
--enforce-eager____\
--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE____\
--kv-transfer-config____'{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\"}'____\
--disable-log-requests____\
--disable-uvicorn-access-log____\
--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN\
--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM \
]"

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/inference-scheduling
