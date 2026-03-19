# PRECISE PREFIX CACHE AWARE ROUTING WELL LIT PATH FOR S390X ARCHITECTURE
# Based on https://github.com/llm-d/llm-d/tree/main/guides/precise-prefix-cache-aware/README.md
# Removed pod monitoring; can be added using LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG
# Removed extra volumes metrics-volume and torch-compile-volume; they are not needed for this model and tested hardware.
# Use LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS and LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES to add them if needed.

# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
export LLMDBENCH_DEPLOY_MODEL_LIST="ibm-granite/granite-3.3-8b-instruct"


# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
export LLMDBENCH_VLLM_COMMON_EXTRA_PVC_NAME=spyre-precompiled-model

export LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME=istio


# Routing configuration (via gaie)
#export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="default-plugins.yaml" (default is "plugins-v2.yaml")
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_SIDECAR_ENABLED=true
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_FLAGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_GAIE_FLAGS
kv-cache-usage-percentage-metric: "vllm:kv_cache_usage_perc"
v: 4  # log verbosity
EOF
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE="precise-prefix-cache-config.yaml"
export LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS
  precise-prefix-cache-config.yaml: |
    apiVersion: inference.networking.x-k8s.io/v1alpha1
    kind: EndpointPickerConfig
    plugins:
      - type: single-profile-handler
      - type: precise-prefix-cache-scorer
        parameters:
          tokenProcessorConfig:
            blockSize: 64
          indexerConfig:
            tokenizersPoolConfig:
              modelName: $LLMDBENCH_DEPLOY_MODEL_LIST
              local: null
              hf: null
              uds:
                socketFile: /tmp/tokenizer/tokenizer-uds.socket
          kvEventsConfig:
            topicFilter: "kv@"
            concurrency: 4
            discoverPods: false
            zmqEndpoint: "tcp://*:5557"
      - type: kv-cache-utilization-scorer
      - type: queue-scorer
      - type: max-score-picker
    schedulingProfiles:
      - name: default
        plugins:
          - pluginRef: precise-prefix-cache-scorer
            weight: 3.0
          - pluginRef: kv-cache-utilization-scorer
            weight: 2.0
          - pluginRef: queue-scorer
            weight: 2.0
          - pluginRef: max-score-picker
EOF
export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL_PROVIDER_CONFIG=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL_PROVIDER_CONFIG
destinationRule:
  host: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL-gaie-epp
  trafficPolicy:
    connectionPool:
      http:
        http1MaxPendingRequests: 256000
        maxRequestsPerConnection: 256000
        http2MaxRequests: 256000
        idleTimeout: "900s"
      tcp:
        maxConnections: 256000
        maxConnectionDuration: "1800s"
        connectTimeout: "900s"
EOF

#export LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME=data-science-gateway-class
#export LLMDBENCH_VLLM_MODELSERVICE_INFERENCEPOOL_API=inference.networking.x-k8s.io/v1alpha2

# Routing configuration (via modelservice)
#export LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL=true # already the default
#export LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR=nixlv2 # already the default

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU... WILL WORK FOR OpenShift, Kubernetes and GKE)
export LLMDBENCH_VLLM_COMMON_AFFINITY=                                                     # OpenShift

#             Uncomment to use hostNetwork (only ONE PODE PER NODE)
#export LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG=$(mktemp)
#cat << EOF > ${LLMDBENCH_VLLM_MODELSERVICE_EXTRA_POD_CONFIG}
#   hostNetwork: true
#   dnsPolicy: ClusterFirstWithHostNet
#EOF

# Common parameters across standalone and llm-d (prefill and decode) pods (specific for s390x architecture and selected model)
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=ibm.com/spyre_vf
export LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS=512
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768
export LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ=32
export LLMDBENCH_VLLM_COMMON_CPU_MEM=220Gi
export LLMDBENCH_VLLM_COMMON_SHM_MEM=64Gi
export LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM=4
export LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM=1

export LLMDBENCH_VLLM_COMMON_REPLICAS=1

# vllm-spyre pod scheduler for s390x 
export LLMDBENCH_VLLM_COMMON_POD_SCHEDULER=default-scheduler


export LLMDBENCH_VLLM_COMMON_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"

# The following variables are automatically populated on the pod: VLLM_BLOCK_SIZE,
#                                                                 VLLM_MAX_MODEL_LEN,
#                                                                 VLLM_LOAD_FORMAT,
#                                                                 VLLM_ACCELERATOR_MEM_UTIL,
#                                                                 VLLM_MAX_NUM_SEQ,
#                                                                 VLLM_TENSOR_PARALLELISM,
#                                                                 VLLM_MAX_NUM_BATCHED_TOKENS,
#                                                                 VLLM_WORKER_MULTIPROC_METHOD,
#                                                                 VLLM_SERVER_DEV_MODE,
#                                                                 VLLM_LOGGING_LEVEL,
#                                                                 VLLM_CACHE_ROOT,
#                                                                 VLLM_INFERENCE_PORT,
#                                                                 VLLM_METRICS_PORT,
#                                                                 VLLM_ALLOW_LONG_MAX_MODEL_LEN,
#                                                                 VLLM_NIXL_SIDE_CHANNEL_PORT,
#                                                                 VLLM_NIXL_SIDE_CHANNEL_HOST,
#                                                                 UCX_TLS,
#                                                                 UCX_SOCKADDR_TLS_PRIORITY,
#                                                                 POD_IP
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: SERVED_MODEL_NAME
  value: REPLACE_ENV_LLMDBENCH_DEPLOY_MODEL_LIST
- name: HF_HUB_OFFLINE
  value: '0'
- name: VLLM_SPYRE_PERF_METRIC_LOGGING_ENABLED
  value: '1'
- name: TORCH_SENDNN_CACHE_ENABLE
  value: '1'
- name: TORCH_SENDNN_CACHE_DIR
  value: /opt/ibm/spyre/models/cache/
- name: PORT
  value: "REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT"
- name: VLLM_SPYRE_DYNAMO_BACKEND
  value: 'sendnn'
- name: VLLM_SPYRE_USE_CB
  value: '1'
- name: VLLM_SPYRE_USE_CHUNKED_PREFILL
  value: '1'
- name: VLLM_DT_CHUNK_LEN
  value: '512'
- name: DTLOG_LEVEL
  value: error
- name: TORCH_SENDNN_LOG
  value: CRITICAL
- name: PYTHONHASHSEED
  value: '67'
- name: VLLM_SPYRE_REQUIRE_PRECOMPILED_DECODERS
  value: '1'
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG}
ports:
  - containerPort: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT
    name: metrics
    protocol: TCP
securityContext:
  capabilities:
    add:
    - "IPC_LOCK"
    - "SYS_RAWIO"
    - "NET_ADMIN"
    - "NET_RAW"
  runAsGroup: 0
  runAsUser: 0
imagePullPolicy: Always
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
- name: dshm
  mountPath: /dev/shm
- name: preprocesses
  mountPath: /setup/preprocess
EOF

export LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES=$(mktemp)
cat << EOF > ${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES}
- name: preprocesses
  configMap:
    defaultMode: 0755
    name: llm-d-benchmark-preprocesses
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM
EOF

# Prefill parameters
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=0

# Decode parameters
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=2
export LLMDBENCH_LLMD_ROUTINGSIDECAR_ENABLED=false
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=$LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_RESOURCE=$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR=$LLMDBENCH_VLLM_COMMON_CPU_NR
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM=$LLMDBENCH_VLLM_COMMON_CPU_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_SHM_MEM=$LLMDBENCH_VLLM_COMMON_SHM_MEM
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML=${LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML}
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG=${LLMDBENCH_VLLM_COMMON_EXTRA_CONTAINER_CONFIG}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUME_MOUNTS}
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES=${LLMDBENCH_VLLM_COMMON_EXTRA_VOLUMES}
#export LLMDBENCH_VLLM_COMMON_PODANNOTATIONS='sidecar.istio.io/inject=false'
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=auto # (automatically calculated to be LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM*LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM)
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS=$LLMDBENCH_VLLM_COMMON_PODANNOTATIONS
#export LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE=$LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=imageDefault
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << 'EOF' > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
--host 0.0.0.0 \
--model /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--port \$VLLM_INFERENCE_PORT \
--max-num-seqs \$LLMDBENCH_VLLM_COMMON_MAX_NUM_SEQ \
--max-model-len \$LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size \$LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM \
--max-num-batched-tokens \$LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS \
--enable-prefix-caching \
--prefix-caching-hash-algo sha256_cbor \
--kv-events-config {"enable_kv_cache_events":true,"publisher":"zmq","endpoint":"tcp://REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_SERVICE_NAME.REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NAMESPACE.svc.cluster.local:5557","topic":"kv@${POD_IP}@QREPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL"}
EOF

# Workload parameters
export LLMDBENCH_HARNESS_NAME=inference-perf
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=shared_prefix_synthetic.yaml

# Local directory to copy benchmark runtime files and results
export LLMDBENCH_CONTROL_WORK_DIR=~/data/precise_prefix_cache_aware