# Shared configuration and validation

# Cluster access
export LLMDBENCH_CLUSTER_URL="${LLMDBENCH_CLUSTER_URL:-auto}"
export LLMDBENCH_CLUSTER_TOKEN="${LLMDBENCH_CLUSTER_TOKEN:-sha256~sVYh-xxx}"

export LLMDBENCH_HF_TOKEN="${LLMDBENCH_HF_TOKEN:-}"

# Images
export LLMDBENCH_IMAGE_REGISTRY=${LLMDBENCH_IMAGE_REGISTRY:-ghcr.io}
export LLMDBENCH_IMAGE_REPO=${LLMDBENCH_IMAGE_REPO:-llm-d}
export LLMDBENCH_IMAGE_NAME=${LLMDBENCH_IMAGE_NAME:-llm-d-benchmark}
export LLMDBENCH_IMAGE_TAG=${LLMDBENCH_IMAGE_TAG:-auto}
export LLMDBENCH_LLMD_IMAGE_REGISTRY=${LLMDBENCH_LLMD_IMAGE_REGISTRY:-ghcr.io}
export LLMDBENCH_LLMD_IMAGE_REPO=${LLMDBENCH_LLMD_IMAGE_REPO:-llm-d}
export LLMDBENCH_LLMD_IMAGE_NAME=${LLMDBENCH_LLMD_IMAGE_REPO:-llm-d}
export LLMDBENCH_LLMD_IMAGE_TAG=${LLMDBENCH_LLMD_IMAGE_TAG:-0.0.8}
export LLMDBENCH_LLMD_MODELSERVICE_IMAGE_REGISTRY=${LLMDBENCH_LLMD_MODELSERVICE_IMAGE_REGISTRY:-ghcr.io}
export LLMDBENCH_LLMD_MODELSERVICE_IMAGE_REPO=${LLMDBENCH_LLMD_MODELSERVICE_IMAGE_REPO:-llm-d}
export LLMDBENCH_LLMD_MODELSERVICE_IMAGE_NAME=${LLMDBENCH_LLMD_MODELSERVICE_IMAGE_NAME:-llm-d-model-service}
export LLMDBENCH_LLMD_MODELSERVICE_IMAGE_TAG=${LLMDBENCH_LLMD_MODELSERVICE_IMAGE_TAG:-0.0.10}
export LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REGISTRY=${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REGISTRY:-ghcr.io}
export LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REPO=${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REPO:-llm-d}
export LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_NAME=${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_NAME:-llm-d-inference-scheduler}
export LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG=${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG:-auto}
export LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_REGISTRY=${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_REGISTRY:-ghcr.io}
export LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_REPO=${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_REPO:-llm-d}
export LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_NAME=${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_NAME:-llm-d-routing-sidecar}
export LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_TAG=${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_TAG:-0.0.6}
export LLMDBENCH_LLMD_INFERENCESIM_IMAGE_REGISTRY=${LLMDBENCH_LLMD_INFERENCESIM_IMAGE_REGISTRY:-ghcr.io}
export LLMDBENCH_LLMD_INFERENCESIM_IMAGE_REPO=${LLMDBENCH_LLMD_INFERENCESIM_IMAGE_REPO:-llm-d}
export LLMDBENCH_LLMD_INFERENCESIM_IMAGE_NAME=${LLMDBENCH_LLMD_INFERENCESIM_IMAGE_NAME:-llm-d-inference-sim}
export LLMDBENCH_LLMD_INFERENCESIM_IMAGE_TAG=${LLMDBENCH_LLMD_INFERENCESIM_IMAGE_TAG:-v0.1.2}
export LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY=${LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY:-docker.io}
export LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO=${LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO:-vllm}
export LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME=${LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME:-vllm-openai}
export LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG=${LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG:-latest}

# External repositories
export LLMDBENCH_DEPLOYER_GIT_REPO="${LLMDBENCH_DEPLOYER_GIT_REPO:-https://github.com/llm-d/llm-d-deployer.git}"
export LLMDBENCH_DEPLOYER_DIR="${LLMDBENCH_DEPLOYER_DIR:-/tmp}"
export LLMDBENCH_DEPLOYER_GIT_BRANCH="${LLMDBENCH_DEPLOYER_GIT_BRANCH:-main}"

export LLMDBENCH_INFRA_GIT_REPO="${LLMDBENCH_INFRA_GIT_REPO:-https://github.com/llm-d-incubation/llm-d-infra.git}"
export LLMDBENCH_INFRA_DIR="${LLMDBENCH_INFRA_DIR:-/tmp}"
export LLMDBENCH_INFRA_GIT_BRANCH="${LLMDBENCH_INFRA_GIT_BRANCH:-main}"

export LLMDBENCH_HARNESS_GIT_REPO="${LLMDBENCH_HARNESS_GIT_REPO:-auto}"
export LLMDBENCH_HARNESS_DIR="${LLMDBENCH_HARNESS_DIR:-/tmp}"
export LLMDBENCH_HARNESS_GIT_BRANCH="${LLMDBENCH_HARNESS_GIT_BRANCH:-main}"

# Applicable to both standalone and deployer
export LLMDBENCH_VLLM_COMMON_NAMESPACE="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-llmdbench}"
export LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT="${LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT:-default}"

export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=${LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE:-nvidia.com/gpu}
export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=${LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE:-}
export LLMDBENCH_VLLM_COMMON_NETWORK_NR=${LLMDBENCH_VLLM_COMMON_NETWORK_NR:-}
export LLMDBENCH_VLLM_COMMON_AFFINITY=${LLMDBENCH_VLLM_COMMON_AFFINITY:-${LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE}.product:NVIDIA-H100-80GB-HBM3}
export LLMDBENCH_VLLM_COMMON_REPLICAS=${LLMDBENCH_VLLM_COMMON_REPLICAS:-1}
export LLMDBENCH_VLLM_COMMON_PERSISTENCE_ENABLED=${LLMDBENCH_VLLM_COMMON_PERSISTENCE_ENABLED:-true}
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=${LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR:-1}
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL=${LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL:-0.95}
export LLMDBENCH_VLLM_COMMON_CPU_NR=${LLMDBENCH_VLLM_COMMON_CPU_NR:-4}
export LLMDBENCH_VLLM_COMMON_CPU_MEM=${LLMDBENCH_VLLM_COMMON_CPU_MEM:-40Gi}
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=${LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN:-16384}
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=${LLMDBENCH_VLLM_COMMON_BLOCK_SIZE:-64}
export LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS=${LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS:-4096}
export LLMDBENCH_VLLM_COMMON_PVC_NAME=${LLMDBENCH_VLLM_COMMON_PVC_NAME:-"model-pvc"}
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS="${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS:-default}"
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE="${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE:-300Gi}"
export LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT=${LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT:-"2400"}
export LLMDBENCH_VLLM_COMMON_HF_TOKEN_KEY="${LLMDBENCH_VLLM_COMMON_HF_TOKEN_KEY:-"HF_TOKEN"}"
export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME:-"llm-d-hf-token"}
export LLMDBENCH_VLLM_COMMON_INFERENCE_PORT=${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT:-"8000"}
export LLMDBENCH_VLLM_COMMON_FQDN=${LLMDBENCH_VLLM_COMMON_FQDN:-".svc.cluster.local"}
export LLMDBENCH_VLLM_COMMON_TIMEOUT=${LLMDBENCH_VLLM_COMMON_TIMEOUT:-3600}
export LLMDBENCH_VLLM_COMMON_ANNOTATIONS=${LLMDBENCH_VLLM_COMMON_ANNOTATIONS:-deployed-by:$(id -un),deployer:llm-d-benchmark}
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=${LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML:-LLMDBENCH_VLLM_STANDALONE_VLLM_ALLOW_LONG_MAX_MODEL_LEN,LLMDBENCH_VLLM_STANDALONE_VLLM_SERVER_DEV_MODE}
export LLMDBENCH_VLLM_COMMON_INITIAL_DELAY_PROBE=${LLMDBENCH_VLLM_COMMON_INITIAL_DELAY_PROBE:-240}

# Standalone-specific parameters
export LLMDBENCH_VLLM_STANDALONE_PREPROCESS=${LLMDBENCH_VLLM_STANDALONE_PREPROCESS:-true}
export LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT=${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT:-/model-storage}
export LLMDBENCH_VLLM_STANDALONE_PREPROCESS=${LLMDBENCH_VLLM_STANDALONE_PREPROCESS:-true}
export LLMDBENCH_VLLM_STANDALONE_ROUTE=${LLMDBENCH_VLLM_STANDALONE_ROUTE:-1}
export LLMDBENCH_VLLM_STANDALONE_HTTPROUTE=${LLMDBENCH_VLLM_STANDALONE_HTTPROUTE:-0}
export LLMDBENCH_VLLM_STANDALONE_VLLM_ALLOW_LONG_MAX_MODEL_LEN=${LLMDBENCH_VLLM_STANDALONE_VLLM_ALLOW_LONG_MAX_MODEL_LEN:-1}
# VLLM_SERVER_DEV_MODE="1" necessary to enable sleep/wake_up server endpoints
export LLMDBENCH_VLLM_STANDALONE_VLLM_SERVER_DEV_MODE=${LLMDBENCH_VLLM_STANDALONE_VLLM_SERVER_DEV_MODE:-1}
export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT:-"auto"}
export LLMDBENCH_VLLM_STANDALONE_ARGS=${LLMDBENCH_VLLM_STANDALONE_ARGS:-"REPLACE_ENV_LLMDBENCH_VLLM_STANDALONE_PREPROCESS____;____vllm____serve____REPLACE_MODEL____--enable-sleep-mode____--load-format____REPLACE_ENV_LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT____--port____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--disable-log-requests____--gpu-memory-utilization____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL____--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR"}
export LLMDBENCH_VLLM_STANDALONE_EPHEMERAL_STORAGE=${LLMDBENCH_VLLM_STANDALONE_EPHEMERAL_STORAGE:-"20Gi"}

# Deployer-specific parameters
export LLMDBENCH_VLLM_DEPLOYER_VALUES_FILE=${LLMDBENCH_VLLM_DEPLOYER_VALUES_FILE:-"fromenv"}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS:-1}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_EXTRA_ARGS=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_EXTRA_ARGS:-"[--disable-log-requests]"}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR:-$LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_MEM_UTIL=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_MEM_UTIL:-$LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_RESOURCE=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_RESOURCE:-$LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_NR=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_NR:-$LLMDBENCH_VLLM_COMMON_NETWORK_NR}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_NR=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_NR:-$LLMDBENCH_VLLM_COMMON_CPU_NR}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_MEM=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_MEM:-$LLMDBENCH_VLLM_COMMON_CPU_MEM}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_INFERENCE_PORT=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_INFERENCE_PORT:-${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS=${LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS:-1}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_EXTRA_ARGS=${LLMDBENCH_VLLM_DEPLOYER_DECODE_EXTRA_ARGS:-"[--disable-log-requests]"}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_NR=${LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_NR:-$LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_MEM_UTIL=${LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_MEM_UTIL:-$LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_RESOURCE=${LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_RESOURCE:-$LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_NR=${LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_NR:-$LLMDBENCH_VLLM_COMMON_NETWORK_NR}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_NR=${LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_NR:-$LLMDBENCH_VLLM_COMMON_CPU_NR}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_MEM=${LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_MEM:-$LLMDBENCH_VLLM_COMMON_CPU_MEM}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_INFERENCE_PORT=${LLMDBENCH_VLLM_DEPLOYER_DECODE_INFERENCE_PORT:-"8200"}
export LLMDBENCH_VLLM_DEPLOYER_GATEWAY_CLASS_NAME=${LLMDBENCH_VLLM_DEPLOYER_GATEWAY_CLASS_NAME:-kgateway}
export LLMDBENCH_VLLM_DEPLOYER_RELEASE=${LLMDBENCH_VLLM_DEPLOYER_RELEASE:-"llm-d"}
export LLMDBENCH_VLLM_DEPLOYER_ROUTE=${LLMDBENCH_VLLM_DEPLOYER_ROUTE:-1}

# FIXME (start) delete after removal of llm-d-deployer
export LLMDBENCH_VLLM_DEPLOYER_BASECONFIGMAPREFNAME=${LLMDBENCH_VLLM_DEPLOYER_BASECONFIGMAPREFNAME:-"basic-gpu-with-nixl-and-redis-lookup-preset"}
export LLMDBENCH_VLLM_DEPLOYER_MODELSERVICE_REPLICAS=${LLMDBENCH_VLLM_DEPLOYER_MODELSERVICE_REPLICAS:-1}
export LLMDBENCH_VLLM_DEPLOYER_RECONFIGURE_GATEWAY_AFTER_DEPLOY=${LLMDBENCH_VLLM_DEPLOYER_RECONFIGURE_GATEWAY_AFTER_DEPLOY:-0}
# FIXME (end) delete after removal of llm-d-deployer

# Endpoint Picker Parameters, Deployer-specific
export LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS=${LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS:-"default"}

# FIXME (start) delete after removal of llm-d-deployer
export LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_KVCACHE_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_KVCACHE_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_KVCACHE_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_KVCACHE_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_PREFIX_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_PREFIX_AWARE_SCORER:-true}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFIX_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFIX_AWARE_SCORER_WEIGHT:-2}
export LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_LOAD_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_LOAD_AWARE_SCORER:-true}
export LLMDBENCH_VLLM_DEPLOYER_EPP_LOAD_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_LOAD_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_SESSION_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_ENABLE_SESSION_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_SESSION_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_SESSION_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PD_ENABLED=${LLMDBENCH_VLLM_DEPLOYER_EPP_PD_ENABLED:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PD_PROMPT_LEN_THRESHOLD=${LLMDBENCH_VLLM_DEPLOYER_EPP_PD_PROMPT_LEN_THRESHOLD:-10}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_KVCACHE_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_KVCACHE_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_KVCACHE_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_KVCACHE_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_LOAD_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_LOAD_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_LOAD_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_LOAD_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_PREFIX_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_PREFIX_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_PREFIX_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_PREFIX_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_SESSION_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_ENABLE_SESSION_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_SESSION_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_PREFILL_SESSION_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_KVCACHE_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_KVCACHE_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_KVCACHE_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_KVCACHE_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_LOAD_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_LOAD_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_LOAD_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_LOAD_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_PREFIX_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_PREFIX_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_PREFIX_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_PREFIX_AWARE_SCORER_WEIGHT:-1}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_SESSION_AWARE_SCORER=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_ENABLE_SESSION_AWARE_SCORER:-false}
export LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_SESSION_AWARE_SCORER_WEIGHT=${LLMDBENCH_VLLM_DEPLOYER_EPP_DECODE_SESSION_AWARE_SCORER_WEIGHT:-1}
# FIXME (end) delete after removal of llm-d-deployer

# Modelservice (helm chart) specific parameters
export LLMDBENCH_VLLM_MODELSERVICE_RELEASE=${LLMDBENCH_VLLM_MODELSERVICE_RELEASE:-"llmdbench"}
export LLMDBENCH_VLLM_MODELSERVICE_VALUES_FILE=${LLMDBENCH_VLLM_MODELSERVICE_VALUES_FILE:-"default-values.yaml"}
export LLMDBENCH_VLLM_MODELSERVICE_ADDITIONAL_SETS=${LLMDBENCH_VLLM_MODELSERVICE_ADDITIONAL_SETS:-""}
export LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION=${LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION:-auto}
export LLMDBENCH_VLLM_MODELSERVICE_CHART=${LLMDBENCH_VLLM_MODELSERVICE_CHART:-"llm-d-modelservice"}
export LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY=${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY:-"llm-d-modelservice"}
export LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY_URL=${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY_URL:-"https://llm-d-incubation.github.io/llm-d-modelservice/"}

# Harness and Experiment
export LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST=$(ls ${LLMDBENCH_MAIN_DIR}/workload/profiles/)
export LLMDBENCH_HARNESS_NAME=${LLMDBENCH_HARNESS_NAME:-vllm-benchmark}
export LLMDBENCH_HARNESS_EXECUTABLE=${LLMDBENCH_HARNESS_EXECUTABLE:-llm-d-benchmark.sh}
export LLMDBENCH_HARNESS_CONDA_ENV_NAME="${LLMDBENCH_HARNESS_CONDA_ENV_NAME:-${LLMDBENCH_HARNESS_NAME}-env}"
export LLMDBENCH_HARNESS_WAIT_TIMEOUT=${LLMDBENCH_HARNESS_WAIT_TIMEOUT:-900}
# FIXME: Attempt to make LLMDBENCH_VLLM_COMMON_NAMESPACE and LLMDBENCH_HARNESS_NAMESPACE different (need to be same now)
#export LLMDBENCH_HARNESS_NAMESPACE=${LLMDBENCH_HARNESS_NAMESPACE:-${LLMDBENCH_HARNESS_NAME}}
export LLMDBENCH_HARNESS_NAMESPACE=${LLMDBENCH_VLLM_COMMON_NAMESPACE}
export LLMDBENCH_HARNESS_SERVICE_ACCOUNT=${LLMDBENCH_HARNESS_SERVICE_ACCOUNT:-${LLMDBENCH_HARNESS_NAME}-runner}
export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE="${LLMDBENCH_HARNESS_EXPERIMENT_PROFILE:-simple-random.yaml}"
export LLMDBENCH_HARNESS_PVC_NAME="${LLMDBENCH_HARNESS_PVC_NAME:-"workload-pvc"}"
export LLMDBENCH_HARNESS_PVC_SIZE="${LLMDBENCH_HARNESS_PVC_SIZE:-20Gi}"
export LLMDBENCH_HARNESS_CONTAINER_IMAGE=${LLMDBENCH_HARNESS_CONTAINER_IMAGE:-lmcache/lmcache-benchmark:main}
export LLMDBENCH_HARNESS_SKIP_RUN=${LLMDBENCH_HARNESS_SKIP_RUN:-}

export LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME=${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME:-llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher}
export LLMDBENCH_RUN_EXPERIMENT_ID=${LLMDBENCH_RUN_EXPERIMENT_ID:-$(date +%s)}
export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX=${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX:-/requests}
export LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY="${LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY:-0}"

# LLM-D-Benchmark deployment specific variables
export LLMDBENCH_DEPLOY_MODEL_LIST=${LLMDBENCH_DEPLOY_MODEL_LIST:-"llama-3b"}
export LLMDBENCH_DEPLOY_METHODS=${LLMDBENCH_DEPLOY_METHODS:-"modelservice"}

# Control variables
export LLMDBENCH_CONTROL_CLUSTER_NAME=${LLMDBENCH_CONTROL_CLUSTER_NAME:-$(echo ${LLMDBENCH_CLUSTER_URL} | cut -d '.' -f 2)}
export LLMDBENCH_CONTROL_ENVVAR_DISPLAYED=${LLMDBENCH_CONTROL_ENVVAR_DISPLAYED:-0}
export LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED=${LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED:-0}
export LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED=${LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED:-0}
export LLMDBENCH_CONTROL_PERMISSIONS_CHECKED=${LLMDBENCH_CONTROL_PERMISSIONS_CHECKED:-0}
export LLMDBENCH_CONTROL_WARNING_DISPLAYED=${LLMDBENCH_CONTROL_WARNING_DISPLAYED:-0}
export LLMDBENCH_CONTROL_STANDUP_ALL_STEPS=${LLMDBENCH_CONTROL_STANDUP_ALL_STEPS:-0}
export LLMDBENCH_CONTROL_WAIT_TIMEOUT=${LLMDBENCH_CONTROL_WAIT_TIMEOUT:-900}
export LLMDBENCH_CONTROL_CHECK_CLUSTER_AUTHORIZATIONS=${LLMDBENCH_CONTROL_CHECK_CLUSTER_AUTHORIZATIONS:-0}
export LLMDBENCH_CONTROL_RESOURCE_LIST=deployment,httproute,route,service,gateway,gatewayparameters,inferencepool,inferencemodel,cm,ing,pod,job

function model_attribute {
  local model=$1
  local attribute=$2

  # Do not use associative arrays. Not supported by MacOS with older bash versions

  case "$model" in
    "llama-3b")
      local model=meta-llama/Llama-3.2-3B-Instruct ;;
    "llama-8b")
      local model=meta-llama/Llama-3.1-8B-Instruct ;;
    "llama-70b")
      local model=meta-llama/Llama-3.1-70B-Instruct ;;
    "llama-17b")
      local model=meta-llama/Llama-4-Scout-17B-16E-Instruct ;;
    *)
      true ;;
  esac

  local modelcomponents=$(echo $model | cut -d '/' -f 2 |  tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e 's^qwen^qwen-^g' -e 's^-^\n^g')
  local provider=$(echo $model | cut -d '/' -f 1)
  local type=$(echo "${modelcomponents}" | grep -Ei "nstruct|hf|chat|speech|vision")
  local parameters=$(echo "${modelcomponents}" | grep -Ei "[0-9].*b|[0-9].*m" | $LLMDBENCH_CONTROL_SCMD -e 's^a^^' -e 's^\.^p^')
  local majorversion=$(echo "${modelcomponents}" | grep -Ei "^[0-9]" | grep -Evi "b|E" |  $LLMDBENCH_CONTROL_SCMD -e "s/$parameters//g" | cut -d '.' -f 1)
  local kind=$(echo "${modelcomponents}" | head -n 1 | cut -d '/' -f 1)
  local as_label=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e "s^/^-^g")
  local label=$(echo ${kind}-${majorversion}-${parameters} | $LLMDBENCH_CONTROL_SCMD -e 's^-$^^g' -e 's^--^^g')
  local as_label=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e "s^/^-^g" -e "s^\.^-^g")
  local folder=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e 's^/^_^g' -e 's^-^_^g')

  if [[ $attribute != "model" ]];
  then
    echo ${!attribute} | tr '[:upper:]' '[:lower:]'
  else
    echo ${!attribute}
  fi
}
export -f model_attribute

function resolve_harness_git_repo {
  local harness_name=$1

  if [[ $LLMDBENCH_HARNESS_GIT_REPO == "auto" ]]; then
    case "$harness_name" in
      "fmperf")
          echo "https://github.com/fmperf-project/fmperf.git" ;;
      "vllm"|"vllm-benchmark")
          echo "https://github.com/vllm-project/vllm.git";;
      "inference-perf")
          echo "https://github.com/kubernetes-sigs/inference-perf.git";;
      *)
          echo "Unknown harness: $harness_name"
          exit 1;;
    esac
  else
    echo "${LLMDBENCH_HARNESS_GIT_REPO}"
  fi
}

is_oc=$(which oc || true)
if [[ -z $is_oc ]]; then
  export LLMDBENCH_CONTROL_KCMD=${LLMDBENCH_CONTROL_KCMD:-kubectl}
else
  export LLMDBENCH_CONTROL_KCMD=${LLMDBENCH_CONTROL_KCMD:-oc}
fi

export LLMDBENCH_CONTROL_HCMD=helm
export LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED=${LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED:-0}

is_mac=$(uname -s | grep -i darwin || true)
if [[ ! -z $is_mac ]]
then
    export LLMDBENCH_CONTROL_DEPLOY_HOST_OS=mac
    export LLMDBENCH_BASE64_ARGS=""
  is_gsed=$(which gsed || true)
  if [[ -z ${is_gsed} ]]; then
    brew install gnu-sed
  fi
  export LLMDBENCH_CONTROL_SCMD=gsed
else
    export LLMDBENCH_CONTROL_DEPLOY_HOST_OS=linux
    export LLMDBENCH_BASE64_ARGS="-w0"
    export LLMDBENCH_CONTROL_SCMD=sed
fi

export LLMDBENCH_CONTROL_PCMD=${LLMDBENCH_CONTROL_PCMD:-python3}
is_podman=$(which podman || true)
if [[ ! -z ${is_podman} ]]; then
  export LLMDBENCH_CONTROL_CCMD=podman
else
  is_docker=$(which docker || true)
  if [[ ! -z ${is_docker} ]]; then
    export LLMDBENCH_CONTROL_CCMD=docker
  fi
fi

if [[ $LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED -eq 0 ]]
then
  touch ~/.llmdbench_dependencies_checked
  deplist="$LLMDBENCH_CONTROL_SCMD $LLMDBENCH_CONTROL_PCMD $(echo $LLMDBENCH_CONTROL_KCMD | awk '{ print $1}') $(echo $LLMDBENCH_CONTROL_HCMD | awk '{ print $1}') helmfile kubectl kustomize rsync"
  for req in $deplist; do
    is_checked=$(grep "$req already installed" ~/.llmdbench_dependencies_checked || true)
    if [[ -z ${is_checked} ]]; then
      echo -n "Checking dependency \"${req}\"..."
      is_req=$(which ${req} || true)
      if [[ -z ${is_req} ]]; then
        echo "❌ Dependency \"${req}\" is missing. Please run \"$LLMDBENCH_MAIN_DIR/setup/install_deps.sh\" and try again."
        exit 1
      else
        echo "$req already installed" >> ~/.llmdbench_dependencies_checked
      fi
      echo "done"
    fi
  done
  echo
  is_helmdiff=$($LLMDBENCH_CONTROL_HCMD plugin list | grep diff || true)
  if [[ -z $is_helmdiff ]]; then
    helm plugin install https://github.com/databus23/helm-diff
  fi
  export LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED=1
fi

function get_image {
  local image_registry=$1
  local image_repo=$2
  local image_name=$3
  local image_tag=$4
  local tag_only=${5:-0}

  is_latest_tag=$image_tag
  if [[ $image_tag == "auto" ]]; then
    if [[ $LLMDBENCH_CONTROL_CCMD == "podman" ]]; then
      is_latest_tag=$($LLMDBENCH_CONTROL_CCMD search --list-tags ${image_registry}/${image_repo}/${image_name} | tail -1 | awk '{ print $2 }' || true)
    else
      is_latest_tag=$(skopeo list-tags docker://${image_registry}/${image_repo}/${image_name} | jq -r .Tags[] | tail -1)
    fi
    if [[ -z ${is_latest_tag} ]]; then
      echo "❌ Unable to find latest tag for image \"${image_registry}/${image_repo}/${image_name}\""
      exit 1
    fi
  fi
  if [[ $tag_only -eq 1 ]]; then
    echo ${is_latest_tag}
  else
    echo $image_registry/$image_repo/${image_name}:${is_latest_tag}
  fi
}

if [[ $LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED -eq 0 ]]; then
  return 0
fi

if [[ ! -z $LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO ]]; then
  export LLMDBENCH_DEPLOY_SCENARIO=$LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO
fi

if [[ ! -z $LLMDBENCH_DEPLOY_SCENARIO ]]; then
  if [[ "$LLMDBENCH_DEPLOY_SCENARIO" == /* ]]; then
    export LLMDBENCH_SCENARIO_FULL_PATH=$(echo $LLMDBENCH_DEPLOY_SCENARIO'.sh' | $LLMDBENCH_CONTROL_SCMD 's^.sh.sh^.sh^g')
  else
    export LLMDBENCH_SCENARIO_FULL_PATH=$(echo ${LLMDBENCH_MAIN_DIR}/scenarios/$LLMDBENCH_DEPLOY_SCENARIO'.sh' | $LLMDBENCH_CONTROL_SCMD 's^.sh.sh^.sh^g')
  fi
  if [[ -f $LLMDBENCH_SCENARIO_FULL_PATH ]]; then
    source $LLMDBENCH_SCENARIO_FULL_PATH
  elif [[ $LLMDBENCH_SCENARIO_FULL_PATH == "${LLMDBENCH_MAIN_DIR}/scenarios/none.sh" ]]; then
    true
  else
    echo "❌ Scenario file \"$LLMDBENCH_SCENARIO_FULL_PATH\" could not be found."
    exit 1
  fi
fi

if [[ "$LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS" == /* ]]; then
  export LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS_FULL_PATH=$(echo $LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS'.yaml' | $LLMDBENCH_CONTROL_SCMD 's^.yaml.yaml^.yaml^g')
else
  export LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS_FULL_PATH=$(echo ${LLMDBENCH_MAIN_DIR}/setup/presets/gaie/$LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS'.yaml' | $LLMDBENCH_CONTROL_SCMD 's^.yaml.yaml^.yaml^g')
fi
if [[ ! -f $LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS_FULL_PATH ]]; then
  echo "❌ GAIE presets file \"$LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS_FULL_PATH\" could not be found."
  exit 1
else
  export LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS=$(echo $LLMDBENCH_VLLM_DEPLOYER_GAIE_PRESETS_FULL_PATH | rev | cut -d '/' -f 1 | rev)
fi

overridevarlist=$(env | grep _CLIOVERRIDE_ | cut -d '=' -f 1 || true)
if [[ -n "$overridevarlist" ]]; then
  for overridevar in $overridevarlist; do
    actualvar=$(echo "$overridevar" | sed 's/_CLIOVERRIDE//g')

    if [[ -n "${!overridevar:-}" ]]; then
      export $actualvar=${!overridevar}
      if [[ "${LLMDBENCH_CONTROL_VERBOSE:-0}" -eq 1 && "${LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED:-0}" -eq 0 ]]; then
        echo "Environment variable $actualvar was overridden by command line options"
      fi
    fi
  done

  export LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED=1
fi

required_vars=("LLMDBENCH_HF_TOKEN")
for var in "${required_vars[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "❌ Environment variable '$var' is not set."
    exit 1
  fi
done

is_csv_model_list=$(echo $LLMDBENCH_DEPLOY_MODEL_LIST | grep ',' || true)
if [[ ! -z $is_csv_model_list ]]; then
    echo "❌ Currently, a comma-separated model list (env var LLMDBENCH_DEPLOY_MODEL_LIST, or  -m/--models) is not supported"
    exit 1
fi

export LLMDBENCH_CONTROL_WORK_DIR=${LLMDBENCH_CONTROL_WORK_DIR:-$(mktemp -d -t ${LLMDBENCH_CONTROL_CLUSTER_NAME}-$(echo $0 | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_CONTROL_SCMD -e 's^.sh^^g' -e 's^./^^g')XXX)}
export LLMDBENCH_CONTROL_WORK_DIR_SET=${LLMDBENCH_CONTROL_WORK_DIR_SET:-0}

function prepare_work_dir {
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/environment
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/harnesses
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles
  for profile_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
    mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$profile_type
  done
}
export -f prepare_work_dir

export LLMDBENCH_CURRENT_STEP=${LLMDBENCH_CURRENT_STEP:-}
if [[ $LLMDBENCH_CURRENT_STEP == "00" && $LLMDBENCH_CONTROL_WORK_DIR_SET -eq 1 && $LLMDBENCH_CONTROL_STANDUP_ALL_STEPS -eq 1 ]]; then
  backup_suffix=$(date +"%Y-%m-%d_%H.%M.%S")
  announce "🗑️  Environment Variable \"LLMDBENCH_CONTROL_WORK_DIR\" was set outside \"setup/env.sh\", all steps were selected on \"setup/standup.sh\" and this is the first step on standup. Moving \"$LLMDBENCH_CONTROL_WORK_DIR\" to \"$(echo $LLMDBENCH_CONTROL_WORK_DIR | $LLMDBENCH_CONTROL_SCMD 's^/$^^').$backup_suffix\"..."
  llmdbench_execute_cmd "mv -f $LLMDBENCH_CONTROL_WORK_DIR $(echo $LLMDBENCH_CONTROL_WORK_DIR | $LLMDBENCH_CONTROL_SCMD 's^/$^^').${backup_suffix}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
fi

prepare_work_dir

if [[ ! -f $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx ]]; then
  if [[ -f ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME} ]]; then
    export LLMDBENCH_CONTROL_KCMD="oc --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME}"
    export LLMDBENCH_CONTROL_HCMD="helm --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME}"
    cp -f ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME} $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx
    export LLMDBENCH_CONTROL_REMOTE_KUBECONFIG_FILENAME=config-${LLMDBENCH_CONTROL_CLUSTER_NAME}
  elif [[ -z $LLMDBENCH_CLUSTER_URL || $LLMDBENCH_CLUSTER_URL == "auto" ]]; then
    current_context=$(${LLMDBENCH_CONTROL_KCMD} config view -o json | jq -r '."current-context"' || true)
    ${LLMDBENCH_CONTROL_KCMD} config view --minify --flatten --raw --context=${current_context} > $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx
    export LLMDBENCH_CONTROL_CLUSTER_NAME=$(echo $current_context | cut -d '/' -f 2 | cut -d '-' -f 2)
    if [[ $LLMDBENCH_CONTROL_WARNING_DISPLAYED -eq 0 ]]; then
      echo ""
      echo "WARNING: environment variable LLMDBENCH_CLUSTER_URL=$LLMDBENCH_CLUSTER_URL. Will attempt to use current context \"${current_context}\"."
      echo ""
      export LLMDBENCH_CONTROL_WARNING_DISPLAYED=1
      sleep 5
    fi
    export LLMDBENCH_CONTROL_REMOTE_KUBECONFIG_FILENAME=config
  else
    current_context=$(${LLMDBENCH_CONTROL_KCMD} config view -o json | jq -r '."current-context"' || true)
    if [[ ${current_context} ]]; then
      ${LLMDBENCH_CONTROL_KCMD} config view --minify --flatten --raw --context=${current_context} > $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx
    else
      echo "ERROR: unable to locate current context"
    fi

    export LLMDBENCH_CONTROL_CLUSTER_NAME=$(echo $current_context | cut -d '/' -f 2 | cut -d '-' -f 2)
    current_namespace=$(echo $current_context | cut -d '/' -f 1)
    current_url=$(echo $current_context | cut -d '/' -f 2 | cut -d ':' -f 1 | $LLMDBENCH_CONTROL_SCMD "s^-^.^g")
    target_url=$(echo $LLMDBENCH_CLUSTER_URL | cut -d '/' -f 3 | $LLMDBENCH_CONTROL_SCMD "s^-^.^g")
    if [[ $current_url != $target_url ]]; then
      ${LLMDBENCH_CONTROL_KCMD} login --token="${LLMDBENCH_CLUSTER_TOKEN}" --server="${LLMDBENCH_CLUSTER_URL}:6443"
    fi

    if [[ $current_namespace != $LLMDBENCH_VLLM_COMMON_NAMESPACE ]]; then
      namespace_exists=$(${LLMDBENCH_CONTROL_KCMD} get namespaces | grep $LLMDBENCH_VLLM_COMMON_NAMESPACE || true)
      if [[ ! -z $namespace_exists ]]; then
        ${LLMDBENCH_CONTROL_KCMD} project $LLMDBENCH_VLLM_COMMON_NAMESPACE
      fi
    fi
    export LLMDBENCH_CONTROL_REMOTE_KUBECONFIG_FILENAME=config
  fi
  export LLMDBENCH_CONTROL_KCMD="oc --kubeconfig $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx"
  export LLMDBENCH_CONTROL_HCMD="helm --kubeconfig $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx"
fi

export LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT=${LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT:-0}
is_ocp=$($LLMDBENCH_CONTROL_KCMD api-resources 2>&1 | grep 'route.openshift.io' || true)
if [[ ! -z ${is_ocp} ]]; then
  export LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT=1
else
  export LLMDBENCH_CONTROL_KCMD=$(echo $LLMDBENCH_CONTROL_KCMD | $LLMDBENCH_CONTROL_SCMD 's^oc ^kubectl ^g')
fi

export LLMDBENCH_USER_IS_ADMIN=1
not_admin=$($LLMDBENCH_CONTROL_KCMD get crds 2>&1 | grep -i Forbidden || true)
if [[ ! -z ${not_admin} ]]; then
  export LLMDBENCH_USER_IS_ADMIN=0
else
  is_ns=$($LLMDBENCH_CONTROL_KCMD get namespace -o name| grep -E "namespace/${LLMDBENCH_VLLM_COMMON_NAMESPACE}$" || true)
  if [[ ! -z ${is_ns} ]]; then
    export LLMDBENCH_CONTROL_PROXY_UID=$($LLMDBENCH_CONTROL_KCMD get namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} -o json | jq -e -r '.metadata.annotations["openshift.io/sa.scc.uid-range"]' | perl -F'/' -lane 'print $F[0]+1');
  fi
fi

if [[ $LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS == "default" && ${LLMDBENCH_CONTROL_CALLER} == "standup.sh" ]]; then
  has_default_sc=$($LLMDBENCH_CONTROL_KCMD get storageclass -o=jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{@.metadata.name}{"\n"}{end}' || true)
  if [[ -z $has_default_sc ]]; then
      echo "ERROR: environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=default, but unable to find a default storage class\""
      exit 1
  fi
  export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=${has_default_sc}
fi

for mt in standalone deployer modelservice; do
  is_env=$(echo $LLMDBENCH_DEPLOY_METHODS | grep $mt || true)
  if [[ -z $is_env ]]; then
    export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_$(echo $mt | tr '[:lower:]' '[:upper:]')_ACTIVE=0
  else
    export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_$(echo $mt | tr '[:lower:]' '[:upper:]')_ACTIVE=1
  fi
done

if [[ $LLMDBENCH_CONTROL_PERMISSIONS_CHECKED -eq 0 && ${LLMDBENCH_CONTROL_CHECK_CLUSTER_AUTHORIZATIONS} -ne 0 ]]; then
  for resource in namespace ${LLMDBENCH_CONTROL_RESOURCE_LIST//,/ }; do
    ra=$($LLMDBENCH_CONTROL_KCMD --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE auth can-i '*' $resource 2>&1 | grep yes || true)
    if [[ -z ${ra} ]]
    then
      echo "ERROR: the current user cannot operate over the resource \"${resource}\""
      exit 1
    fi

    ra=$($LLMDBENCH_CONTROL_KCMD --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE auth can-i patch serviceaccount 2>&1 | grep yes || true)
    if [[ -z ${ra} ]]
    then
      echo "ERROR: the current user cannot operate patch serviceaccount\""
      exit 1
    fi
    export LLMDBENCH_CONTROL_PERMISSIONS_CHECKED=1
  done
fi

export LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL=${SHELL:5}

function llmdbench_execute_cmd {
  set +euo pipefail
  local actual_cmd=$1
  local dry_run=${2:-1}
  local verbose=${3:-0}
  local silent=${4:-1}
  local attempts=${5:-1}
  local fatal=${6:-0}
  local counter=1
  local delay=10

  command_tstamp=$(date +%s%N)
  if [[ ${dry_run} -eq 1 ]]; then
    _msg="---> would have executed the command \"${actual_cmd}\""
    echo ${_msg}
    echo ${_msg} > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_command.log
    return 0
  else
    _msg="---> will execute the command \"${actual_cmd}\""
    echo ${_msg} > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_command.log
    while [[ "${counter}" -le "${attempts}" ]]; do
      command_tstamp=$(date +%s%N)
      if [[ ${verbose} -eq 0 && ${silent} -eq 1 ]]; then
        eval ${actual_cmd} 2> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stderr.log 1> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stdout.log
        local ecode=$?
      elif [[ ${verbose} -eq 0 && ${silent} -eq 0 ]]; then
        eval ${actual_cmd} > >(tee -a $LLMDBENCH_CONTROL_WORK_DIR/setup/commands/${command_tstamp}_stdout.log) 2> >(tee -a $LLMDBENCH_CONTROL_WORK_DIR/setup/commands/${command_tstamp}_stderr.log >&2)
        local ecode=$?
      else
        echo ${_msg}
        eval ${actual_cmd} > >(tee -a $LLMDBENCH_CONTROL_WORK_DIR/setup/commands/${command_tstamp}_stdout.log) 2> >(tee -a $LLMDBENCH_CONTROL_WORK_DIR/setup/commands/${command_tstamp}_stderr.log >&2)
        local ecode=$?
      fi

      if [[ $ecode -ne 0 && ${attempts} -gt 1 ]]
      then
        counter="$(( ${counter} + 1 ))"
        sleep ${delay}
      else
          break
      fi
    done
  fi

  if [[ $ecode -ne 0 ]]
  then
    echo "ERROR while executing command \"${actual_cmd}\""
    echo
    if [[ -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stderr.log ]]; then
      cat ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stderr.log
    else
      echo "(stderr not captured)"
    fi
  fi

  set -euo pipefail

  if [[ ${fatal} -eq 1 ]];
  then
    if [[ ${ecode} -ne 0 ]]
    then
      exit ${ecode}
    fi
  fi

  return ${ecode}
}
export -f llmdbench_execute_cmd

function extract_environment {
  local envlist=$(env | grep ^LLMDBENCH | sort | grep -Ev "TOKEN|USER|PASSWORD|EMAIL")
  if [[ $LLMDBENCH_CONTROL_ENVVAR_DISPLAYED -eq 0 ]]; then
    echo -e "\n\nList of environment variables which will be used"
    echo "$envlist"
    echo -e "\n\n"
    export LLMDBENCH_CONTROL_ENVVAR_DISPLAYED=1
  fi
  echo "$envlist" > ${LLMDBENCH_CONTROL_WORK_DIR}/environment/variables
}
export -f extract_environment

function reconfigure_gateway_after_deploy {
  if [[ $LLMDBENCH_VLLM_DEPLOYER_RECONFIGURE_GATEWAY_AFTER_DEPLOY -eq 1 ]]; then
    if [[ $LLMDBENCH_VLLM_DEPLOYER_GATEWAY_CLASS_NAME == "kgateway" ]]; then
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace kgateway-system delete pod -l kgateway=kgateway" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace kgateway-system  wait --for=condition=Ready=True pod -l kgateway=kgateway" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
  fi
}

function add_annotations {
  local output="REPLACEFIRSTNEWLINE"
  for entry in $(echo $LLMDBENCH_VLLM_COMMON_ANNOTATIONS | $LLMDBENCH_CONTROL_SCMD -e 's^\,^\n^g'); do
    output=$output"REPLACE_NEWLINEREPLACE_SPACESN$(echo ${entry} | $LLMDBENCH_CONTROL_SCMD -e 's^:^: ^g')"
  done

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 || $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_DEPLOYER_ACTIVE -eq 1 ]]; then
    local spacen="        "
  fi

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
    local spacen="      "
  fi

  echo -e ${output} | $LLMDBENCH_CONTROL_SCMD -e 's^REPLACEFIRSTNEWLINEREPLACE_NEWLINEREPLACE_SPACESN^^' -e 's^REPLACE_NEWLINE^\n^g' -e "s^REPLACE_SPACESN^$spacen^g" -e '/^*$/d'

}

function add_additional_env_to_yaml {
  local output="REPLACEFIRSTNEWLINE"
  for envvar in ${LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML//,/ }; do
    output=$output"REPLACE_NEWLINEREPLACE_SPACESN- name: $(echo ${envvar} | $LLMDBENCH_CONTROL_SCMD -e 's^LLMDBENCH_VLLM_STANDALONE_^^g')REPLACE_NEWLINEREPLACE_SPACESVvalue: \"${!envvar}\""
  done

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 || $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_DEPLOYER_ACTIVE -eq 1 ]]; then
    local spacen="        "
    local spacev="          "
  fi

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
    local spacen="      "
    local spacev="        "
  fi

  echo -e ${output} | $LLMDBENCH_CONTROL_SCMD -e 's^REPLACEFIRSTNEWLINEREPLACE_NEWLINEREPLACE_SPACESN^^' -e 's^REPLACE_NEWLINE^\n^g' -e "s^REPLACE_SPACESN^$spacen^g"  -e "s^REPLACE_SPACESV^$spacev^g"  -e '/^*$/d'
}
export -f add_additional_env_to_yaml

function render_string {
  set +euo pipefail
  local string=$1
  local model=${2:-}

  if [[ ! -z $model ]]; then
    echo "s^REPLACE_MODEL^$(model_attribute $model model)^g" > $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  islist=$(echo $string | grep "\[" || true)
  if [[ ! -z $islist ]]; then
    echo "s^____^\", \"^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    echo "s^\[^[ \"^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    echo "s^\]^\" ]^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  else
    echo "s^____^ ^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  for entry in $(echo ${string} | $LLMDBENCH_CONTROL_SCMD -e 's/____/ /g' -e 's^-^\n^g' -e 's^:^\n^g' -e 's^/^\n^g' -e 's^ ^\n^g' -e 's^]^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq); do
    default_value=$(echo $entry | $LLMDBENCH_CONTROL_SCMD -e "s^++++default=^\n^" | tail -1)
    parameter_name=$(echo ${entry} | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_ENV_^\n______^g" -e "s^\"^^g" -e "s^'^^g" | grep "______" | $LLMDBENCH_CONTROL_SCMD -e "s^++++default=.*^^" -e "s^______^^g")
    entry=REPLACE_ENV_${parameter_name}
    value=$(echo ${!parameter_name})
    if [[ -z $value && -z $default_value ]]; then
      echo "ERROR: variable \"$entry\" not defined!"
      exit 1
    fi
    if [[ -z $value && ! -z $default_value ]]; then
      value=$default_value
      echo "s^++++default=$default_value^^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    fi
    echo "s^${entry}^${value}^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  done
  if [[ ! -z $model ]]; then
    echo ${string} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi
  set -euo pipefail
}
export -f render_string

function render_template {
  local template_file_path=$1
  local output_file_path=$2

  rm -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  for entry in $(cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -e 's^-^\n^g' -e 's^:^\n^g' -e 's^ ^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq); do
    render_string $entry
  done
  cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands > $output_file_path
}
export -f render_template

function check_storage_class_and_affinity {
  if [[ ${LLMDBENCH_CONTROL_CALLER} != "standup.sh" ]]; then
    return 0
  fi

  local has_sc=$($LLMDBENCH_CONTROL_KCMD get storageclasses | grep $LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS || true)
  if [[ -z $has_sc ]]; then
    echo "ERROR. Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=$LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS but could not find such storage class"
    return 1
  fi

  local annotation1=$(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
  local annotation2=$(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
  local has_affinity=$($LLMDBENCH_CONTROL_KCMD get nodes -o json | jq -r '.items[].metadata.labels' | grep -E "$annotation1.*$annotation2" || true)
  if [[ -z $has_affinity ]]; then
    echo "ERROR. There are no nodes on this cluster with the label \"${annotation1}:${annotation2}\" (environment variable LLMDBENCH_VLLM_COMMON_AFFINITY)"
    return 1
  fi

}
export -f check_storage_class_and_affinity

function not_valid_ip {

    local  ip=$1
    local  stat=1

    echo ${ip} | grep -q '/'
    if [[ $? -eq 0 ]]; then
        local ip=$(echo $ip | cut -d '/' -f 1)
    fi

    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        OIFS=$IFS
        IFS='.'
        ip=($ip)
        IFS=$OIFS
        [[ ${ip[0]} -le 255 && ${ip[1]} -le 255 && ${ip[2]} -le 255 && ${ip[3]} -le 255 ]]
        stat=$?
    fi
    if [[ $stat -eq 0 ]]; then
      echo $ip
    fi
}
export -f not_valid_ip

function announce {
    # 1 - MESSAGE
    # 2 - LOGFILE
    local message=$(echo "${1}" | tr '\n' ' ' | $LLMDBENCH_CONTROL_SCMD "s/\t\t*/ /g")
    local logfile=${2:-1}

    if [[ ! -z ${logfile} ]]
    then
        if [[ ${logfile} == "silent" || ${logfile} -eq 0 ]]
        then
            echo -e "==> $(date) - ${0} - $message" >> /dev/null
        elif [[ ${logfile} -eq 1 ]]
        then
            echo -e "==> $(date) - ${0} - $message"
        else
            echo -e "==> $(date) - ${0} - $message" >> ${logfile}
        fi
    else
        echo -e "==> $(date) - ${0} - $message"
    fi
}
export -f announce

require_var() {
  local var_name="$1"
  local var_value="$2"
  if [[ -z "${var_value}" ]]; then
    announce "❌ Required variable '${var_name}' is empty"
    exit 1
  fi
}
export -f require_var

create_namespace() {
  local kcmd="$1"
  local namespace="$2"
  require_var "namespace" "${namespace}"
  announce "📦 Creating namespace ${namespace}..."

  is_ns=$($LLMDBENCH_CONTROL_KCMD get namespace -o name| grep -E "namespace/${namespace}$" || true)
  if [[ -z ${is_ns} ]]; then
    llmdbench_execute_cmd "${kcmd} create namespace \"${namespace}\" --dry-run=client -o yaml > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_namespace.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    llmdbench_execute_cmd "${kcmd} apply -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_namespace.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "✅ Namespace ready"
  fi
}
export -f create_namespace

create_or_update_hf_secret() {
  local kcmd="$1"
  local namespace="$2"
  local secret_name="$3"
  local secret_key="$4"
  local hf_token="$5"

  require_var "namespace" "${namespace}"
  require_var "secret_name" "${secret_name}"
  require_var "hf_token" "${hf_token}"

  announce "🔐 Creating/updating HF token secret..."

  llmdbench_execute_cmd "${kcmd} delete secret ${secret_name} -n ${namespace} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "${kcmd} create secret generic \"${secret_name}\" --from-literal=\"${secret_key}=${hf_token}\" --dry-run=client -o yaml > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "${kcmd} apply -n "${namespace}" -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  announce "✅ HF token secret created"
}
export -f create_or_update_hf_secret

#
# vLLM Model Download Utilities
#

validate_and_create_pvc() {
  local kcmd="$1"
  local namespace="$2"
  local download_model="$3"
  local pvc_name="$4"
  local pvc_size="$5"
  local pvc_class="$6"

  require_var "download_model" "${download_model}"
  require_var "pvc_name" "${pvc_name}"
  require_var "pvc_size" "${pvc_size}"
  require_var "pvc_class" "${pvc_class}"

  announce "💾 Provisioning model storage…"

  if [[ "${download_model}" != */* ]]; then
    announce "❌ '${download_model}' is not in Hugging Face format <org>/<repo>"
    exit 1
  fi

  announce "🔍 Checking storage class '${pvc_class}'..."
  if ! ${kcmd} get storageclass "${pvc_class}" &>/dev/null; then
    announce "❌ StorageClass '${pvc_class}' not found"
    exit 1
  fi

  cat << EOF > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_storage_pvc_setup.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${pvc_name}
spec:
  accessModes:
  - ReadWriteMany
  resources:
    requests:
      storage: ${pvc_size}
  storageClassName: ${pvc_class}
  volumeMode: Filesystem
EOF

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -n ${namespace} -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_storage_pvc_setup.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 1 1
}
export -f validate_and_create_pvc

launch_download_job() {
  local kcmd="$1"
  local namespace="$2"
  local secret_name="$3"
  local download_model="$4"
  local model_path="$5"
  local pvc_name="$6"

  require_var "namespace" "${namespace}"
  require_var "secret_name" "${secret_name}"
  require_var "download_model" "${download_model}"
  require_var "model_path" "${model_path}"
  require_var "pvc_name" "${pvc_name}"

  announce "🚀 Launching model download job..."

cat << EOF > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_download_pod_job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: download-model
spec:
  template:
    spec:
      containers:
        - name: downloader
          image: python:3.10
          command: ["/bin/sh", "-c"]
          args:
            - mkdir -p "\${MOUNT_PATH}/\${MODEL_PATH}" && \
              pip install huggingface_hub && \
              export PATH="\${PATH}:\${HOME}/.local/bin" && \
              huggingface-cli login --token "\${HF_TOKEN}" && \
              huggingface-cli download "\${HF_MODEL_ID}" --local-dir "/cache/\${MODEL_PATH}"
          env:
            - name: MODEL_PATH
              value: ${model_path}
            - name: HF_MODEL_ID
              value: ${download_model}
            - name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: ${secret_name}
                  key: HF_TOKEN
            - name: HF_HOME
              value: /tmp/huggingface
            - name: HOME
              value: /tmp
            - name: MOUNT_PATH
              value: /cache
          volumeMounts:
            - name: model-cache
              mountPath: /cache
      restartPolicy: OnFailure
      imagePullPolicy: IfNotPresent
      volumes:
        - name: model-cache
          persistentVolumeClaim:
            claimName: ${pvc_name}
EOF
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -n ${namespace} -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_download_pod_job.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 1 1
}
export -f launch_download_job

wait_for_download_job() {
  local kcmd="$1"
  local namespace="$2"
  local timeout="$3"

  require_var "namespace" "${namespace}"
  require_var "timeout" "${timeout}"

  announce "⏳ Waiting for pod to start model download job ..."
  local pod_name
  pod_name="$(${kcmd} get pod --selector=job-name=download-model -n "${namespace}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"

  if [[ -z "${pod_name}" ]]; then
    announce "🙀 No pod found for the job. Exiting..."
    llmdbench_execute_cmd "${kcmd} logs job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 1 1
  fi

  llmdbench_execute_cmd "${kcmd} wait --for=condition=Ready pod/"${pod_name}" --timeout=60s -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  if [[ $? -ne 0 ]]
  then
    announce "🙀 Pod did not become Ready"
    llmdbench_execute_cmd  "${kcmd} logs job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0 1 0
    exit 1
  fi

  announce "⏳ Waiting up to ${timeout}s for job to complete..."
  llmdbench_execute_cmd "${kcmd} wait --for=condition=complete --timeout="${timeout}"s job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  if [[ $? -ne 0 ]]
  then
    announce "🙀 Download job failed or timed out"
    llmdbench_execute_cmd  "${kcmd} logs job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0 1 0
    exit 1
  fi

  announce "✅ Model downloaded"
}
export -f wait_for_download_job
