# P/D DISAGGREGATION WELL LIT PATH
# Based on https://github.com/llm-d/llm-d/tree/main/guides/pd-disaggregation
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti

# Routing configuration (via gaie)
#export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="default-plugins.yaml" # already the default

# Routing configuration (via modelservice)
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true # (default is "false")
# export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2 # already the default

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU... WILL WORK FOR OpenShift, Kubernetes and GKE)
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200                           # Kubernetes
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-tesla-a100  # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb   # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S                  # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu                                      # ANY GPU (useful for Minikube)

#             Uncomment to request specific network devices
#####export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/roce_gdr
#######export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/ib
#export LLMDBENCH_VLLM_COMMON_NETWORK_NR=4

#             Uncomment to use hostNetwork (only ONE PODE PER NODE)
#export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG=$(mktemp)
#cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG}
#   hostNetwork: true
#   dnsPolicy: ClusterFirstWithHostNet
#EOF

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128

#             Uncomment (###) to select additional network devices (e.g., when multi-nic is enabled)
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: UCX_TLS
  value: "rc,sm,cuda_ipc,cuda_copy,tcp"
- name: UCX_SOCKADDR_TLS_PRIORITY
  value: "tcp"
###- name: UCX_NET_DEVICES
###  value: mlx5_1:1
###- name: NCCL_IB_HCA
###  value: mlx5_1
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NIXL_SIDE_CHANNEL_PORT"
- name: VLLM_NIXL_SIDE_CHANNEL_HOST
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: VLLM_LOGGING_LEVEL
  value: DEBUG
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_NR=32
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_MEM=128Gi
#              Uncomment (###) the following line to enable multi-nic
###export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS=deployed-by:$LLMDBENCH_CONTROL_USERNAME,modelservice:llm-d-benchmark,k8s.v1.cni.cncf.io/networks:multi-nic-compute
#              Uncomment (#####) the following two lines to enable roce/gdr (or switch to rdma/ib for infiniband)
#####export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NETWORK_RESOURCE=rdma/roce_gdr
#####export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NETWORK_NR=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS="[\
--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE____\
--kv-transfer-config____'{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\"}'____\
--disable-log-requests____\
--disable-uvicorn-access-log____\
--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____\
--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM\
]"

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=32
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=128Gi
#              Uncomment (###) the following line to enable multi-nic
###export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=deployed-by:$LLMDBENCH_CONTROL_USERNAME,modelservice:llm-d-benchmark,k8s.v1.cni.cncf.io/networks:multi-nic-compute
#              Uncomment (#####) the following two lines to enable roce/gdr (or switch to rdma/ib for infiniband)
#####export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=rdma/roce_gdr
#####export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_NR=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=vllmServe
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[\
--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE____\
--kv-transfer-config____'{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\"}'____\
--disable-log-requests____\
--disable-uvicorn-access-log____\
--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____\
--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM\
]"

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES}
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM
EOF

# Timeout for benchmark operations
export LLMDBENCH_CONTROL_WAIT_TIMEOUT=900000
export LLMDBENCH_HARNESS_WAIT_TIMEOUT=900000

# Workload parameters
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=random_concurrent.yaml
export LLMDBENCH_HARNESS_NAME=vllm-benchmark

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/pd-disaggregation
