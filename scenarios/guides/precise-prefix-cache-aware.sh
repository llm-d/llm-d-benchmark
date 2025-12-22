# PRECISE PREFIX CACHE AWARE ROUTING WELL LIT PATH
# Based on https://github.com/llm-d/llm-d/tree/main/guides/precise-prefix-cache-aware/README.md
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
#export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="deepseek-ai/DeepSeek-R1-0528"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti

# Routing configuration (via gaie)
#export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="default-plugins.yaml" (default is "plugins-v2.yaml")
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="precise-prefix-cache-aware"

# Routing configuration (via modelservice)
#export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true # already the default
#export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2 # already the default

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU... WILL WORK FOR OpenShift, Kubernetes and GKE)
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200                           # Kubernetes
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-tesla-a100  # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb   # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S                  # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu                                      # ANY GPU (useful for Minikube)

#             Uncomment to use hostNetwork (only ONE PODE PER NODE)
#export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG=$(mktemp)
#cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG}
#   hostNetwork: true
#   dnsPolicy: ClusterFirstWithHostNet
#EOF

# Common parameters across standalone and llm-d (prefill and decode) pods
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=16000
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=64

#             Uncomment (###) to select additional network devices (e.g., when multi-nic is enabled)
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
  value: "rc,sm,cuda_ipc,cuda_copy,tcp"
- name: UCX_SOCKADDR_TLS_PRIORITY
  value: "tcp"
- name: VLLM_NIXL_SIDE_CHANNEL_HOST
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: VLLM_NIXL_SIDE_CHANNEL_PORT
  value: "5557"
- name: VLLM_LOGGING_LEVEL
  value: INFO
- name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
  value: "1"
EOF

export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_CONTAINER_CONFIG}
ports:
  - containerPort: ${LLMDBENCH_VLLM_COMMON_NIXL_SIDE_CHANNEL_PORT}
    protocol: TCP
  - containerPort: ${LLMDBENCH_VLLM_COMMON_METRICS_PORT}
    name: metrics
    protocol: TCP
EOF

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0
#export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR=auto # (automatically calculated to be LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM*LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM)

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=64Gi
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=auto # (automatically calculated to be LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM*LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM)
#              Uncomment (######) the following line to enable multi-nic
######export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=k8s.v1.cni.cncf.io/networks:multi-nic-compute
#              Uncomment (######) the following two lines to enable roce/gdr (or switch to rdma/ib for infiniband)
######export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=rdma/roce_gdr
######export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_NR=16
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--block-size REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM \
--gpu-memory-utilization REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_MEM_UTIL \
--prefix-caching-hash-algo sha256_cbor \
--kv-transfer-config "{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\"}" \
--kv-events-config "{\"enable_kv_cache_events\":true,\"publisher\":\"zmq\",\"endpoint\":\"tcp://REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_SERVICE_NAME.REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NAMESPACE.svc.cluster.local:5557\",\"topic\":\"kv@\${POD_IP}@QREPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL\"}" \
--enforce-eager
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
- name: preprocesses
  mountPath: /setup/preprocess
EOF

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES}
- name: preprocesses
  configMap:
    defaultMode: 320
    name: llm-d-benchmark-preprocesses
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM
EOF

# Workload parameters
export LLMDBENCH_HARNESS_NAME=inference-perf
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=shared_prefix_synthetic.yaml

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/precise_prefix_cache_aware
