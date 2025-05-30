# Shared configuration and validation

# Cluster access
export LLMDBENCH_CLUSTER_URL="${LLMDBENCH_CLUSTER_URL:-auto}"
export LLMDBENCH_CLUSTER_TOKEN="${LLMDBENCH_CLUSTER_TOKEN:-sha256~sVYh-xxx}"

export LLMDBENCH_HF_TOKEN="${LLMDBENCH_HF_TOKEN:-}"

# Image
export LLMDBENCH_IMAGE_REGISTRY=${LLMDBENCH_IMAGE_REGISTRY:-ghcr.io}
export LLMDBENCH_IMAGE_REPO=${LLMDBENCH_IMAGE_REPO:-llm-d/llm-d-benchmark}
export LLMDBENCH_IMAGE_TAG=${LLMDBENCH_IMAGE_TAG:-v0.0.8}

# External repositories
export LLMDBENCH_DEPLOYER_GIT_REPO="${LLMDBENCH_DEPLOYER_GIT_REPO:-https://github.com/llm-d/llm-d-deployer.git}"
export LLMDBENCH_DEPLOYER_DIR="${LLMDBENCH_DEPLOYER_DIR:-/tmp}"
export LLMDBENCH_DEPLOYER_GIT_BRANCH="${LLMDBENCH_DEPLOYER_GIT_BRANCH:-main}"
export LLMDBENCH_FMPERF_GIT_REPO="${LLMDBENCH_FMPERF_GIT_REPO:-https://github.com/fmperf-project/fmperf.git}"
export LLMDBENCH_FMPERF_DIR="${LLMDBENCH_FMPERF_DIR:-/tmp}"
export LLMDBENCH_FMPERF_GIT_BRANCH="${LLMDBENCH_FMPERF_GIT_BRANCH:-main}"

# Applicable to both standalone and deployer
export LLMDBENCH_VLLM_COMMON_NAMESPACE="${LLMDBENCH_VLLM_COMMON_NAMESPACE:-llmdbench}"
export LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT="${LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT:-default}"

export LLMDBENCH_VLLM_COMMON_AFFINITY=${LLMDBENCH_VLLM_COMMON_AFFINITY:-nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3}
export LLMDBENCH_VLLM_COMMON_REPLICAS=${LLMDBENCH_VLLM_COMMON_REPLICAS:-1}
export LLMDBENCH_VLLM_COMMON_PERSISTENCE_ENABLED=${LLMDBENCH_VLLM_COMMON_PERSISTENCE_ENABLED:-true}
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR=${LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR:-1}
export LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL=${LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL:-0.95}
export LLMDBENCH_VLLM_COMMON_CPU_NR=${LLMDBENCH_VLLM_COMMON_CPU_NR:-4}
export LLMDBENCH_VLLM_COMMON_CPU_MEM=${LLMDBENCH_VLLM_COMMON_CPU_MEM:-40Gi}
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=${LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN:-16384}
export LLMDBENCH_VLLM_COMMON_PVC_NAME=${LLMDBENCH_VLLM_COMMON_PVC_NAME:-"model-pvc"}
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS="${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS:-ocs-storagecluster-cephfs}"
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE="${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE:-300Gi}"
export LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT=${LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT:-"2400"}
export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME:-"llm-d-hf-token"}
export LLMDBENCH_VLLM_COMMON_INFERENCE_PORT=${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT:-"8000"}

# Standalone-specific parameters
export LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT=${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT:-/models}
export LLMDBENCH_VLLM_STANDALONE_IMAGE=${LLMDBENCH_VLLM_STANDALONE_IMAGE:-"vllm/vllm-openai:latest"}
export LLMDBENCH_VLLM_STANDALONE_ROUTE=${LLMDBENCH_VLLM_STANDALONE_ROUTE:-1}
export LLMDBENCH_VLLM_STANDALONE_HTTPROUTE=${LLMDBENCH_VLLM_STANDALONE_HTTPROUTE:-0}
export LLMDBENCH_VLLM_STANDALONE_ACCELERATOR_RESOURCE=${LLMDBENCH_VLLM_STANDALONE_ACCELERATOR_RESOURCE:-nvidia.com/gpu}
export LLMDBENCH_VLLM_STANDALONE_ENVVARS_TO_YAML=${LLMDBENCH_VLLM_STANDALONE_ENVVARS_TO_YAML:-LLMDBENCH_VLLM_STANDALONE_VLLM_ALLOW_LONG_MAX_MODEL_LEN}
export LLMDBENCH_VLLM_STANDALONE_VLLM_ALLOW_LONG_MAX_MODEL_LEN=${LLMDBENCH_VLLM_STANDALONE_VLLM_ALLOW_LONG_MAX_MODEL_LEN:-1}
export LLMDBENCH_VLLM_STANDALONE_ARGS=${LLMDBENCH_VLLM_STANDALONE_ARGS:-"vllm____serve____REPLACE_MODEL____--port____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--disable-log-requests____--gpu-memory-utilization____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL____--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR"}

# Deployer-specific parameters
export LLMDBENCH_VLLM_DEPLOYER_VALUES_FILE=${LLMDBENCH_VLLM_DEPLOYER_VALUES_FILE:-"fromenv"}
export LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS=${LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS:-1}
export LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS=${LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS:-1}
export LLMDBENCH_VLLM_DEPLOYER_BASECONFIGMAPREFNAME=${LLMDBENCH_VLLM_DEPLOYER_BASECONFIGMAPREFNAME:-"basic-gpu-with-nixl-and-redis-lookup-preset"}
export LLMDBENCH_VLLM_DEPLOYER_MODELSERVICE_REPLICAS=${LLMDBENCH_VLLM_DEPLOYER_MODELSERVICE_REPLICAS:-1}
export LLMDBENCH_VLLM_DEPLOYER_ROUTE=${LLMDBENCH_VLLM_DEPLOYER_ROUTE:-1}

# Endpoint Picker Parameters, Deployer-specific
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

# Experiments
export LLMDBENCH_FMPERF_CONDA_ENV_NAME="${LLMDBENCH_FMPERF_CONDA_ENV_NAME:-fmperf-env}"
export LLMDBENCH_FMPERF_NAMESPACE=${LLMDBENCH_FMPERF_NAMESPACE:-fmperf}
export LLMDBENCH_FMPERF_SERVICE_ACCOUNT=${LLMDBENCH_FMPERF_SERVICE_ACCOUNT:-fmperf-runner}
export LLMDBENCH_FMPERF_EXPERIMENT_HARNESS="${LLMDBENCH_FMPERF_EXPERIMENT_HARNESS:-llm-d-benchmark.py}"
export LLMDBENCH_FMPERF_EXPERIMENT_PROFILE="${LLMDBENCH_FMPERF_EXPERIMENT_PROFILE:-sanity_short-input.yaml}"
export LLMDBENCH_FMPERF_PVC_NAME="${LLMDBENCH_FMPERF_PVC_NAME:-"workload-pvc"}"
export LLMDBENCH_FMPERF_PVC_SIZE="${LLMDBENCH_FMPERF_PVC_SIZE:-20Gi}"
export LLMDBENCH_FMPERF_CONTAINER_IMAGE=${LLMDBENCH_FMPERF_CONTAINER_IMAGE:-lmcache/lmcache-benchmark:main}
export LLMDBENCH_FMPERF_EXPERIMENT_SKIP=${LLMDBENCH_FMPERF_EXPERIMENT_SKIP:-}

# LLM-D-Benchmark deployment specific variables
export LLMDBENCH_DEPLOY_MODEL_LIST=${LLMDBENCH_DEPLOY_MODEL_LIST:-"llama-3b"}
export LLMDBENCH_DEPLOY_METHODS=${LLMDBENCH_DEPLOY_METHODS:-"deployer"}

# Control variables
export LLMDBENCH_CONTROL_CLUSTER_NAME=${LLMDBENCH_CONTROL_CLUSTER_NAME:-$(echo ${LLMDBENCH_CLUSTER_URL} | cut -d '.' -f 2)}
export LLMDBENCH_CONTROL_ENVVAR_DISPLAYED=${LLMDBENCH_CONTROL_ENVVAR_DISPLAYED:-0}
export LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED=${LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED:-0}
export LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED=${LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED:-0}
export LLMDBENCH_CONTROL_PERMISSIONS_CHECKED=${LLMDBENCH_CONTROL_PERMISSIONS_CHECKED:-0}
export LLMDBENCH_CONTROL_WARNING_DISPLAYED=${LLMDBENCH_CONTROL_WARNING_DISPLAYED:-0}
export LLMDBENCH_CONTROL_WAIT_TIMEOUT=${LLMDBENCH_CONTROL_WAIT_TIMEOUT:-900}
export LLMDBENCH_CONTROL_CHECK_CLUSTER_AUTHORIZATIONS=${LLMDBENCH_CONTROL_CHECK_CLUSTER_AUTHORIZATIONS:-0}
export LLMDBENCH_CONTROL_RESOURCE_LIST=deployment,httproute,route,service,gateway,gatewayparameters,inferencepool,inferencemodel,cm,ing,pod,job
export LLMDBENCH_CONTROL_KCMD=oc
export LLMDBENCH_CONTROL_HCMD=helm
export LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED=${LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED:-0}

is_mac=$(uname -s | grep -i darwin || true)
if [[ ! -z $is_mac ]]
then
    export LLMDBENCH_CONTROL_DEPLOY_HOST_OS=mac
  is_gsed=$(which gsed || true)
  if [[ -z ${is_gsed} ]]; then
    brew install gnu-sed
  fi
  export LLMDBENCH_CONTROL_SCMD=gsed
else
    export LLMDBENCH_CONTROL_DEPLOY_HOST_OS=linux
    export LLMDBENCH_CONTROL_SCMD=sed
fi

export LLMDBENCH_CONTROL_PCMD=${LLMDBENCH_CONTROL_PCMD:-python3}
export LLMDBENCH_CONTROL_CCMD=${LLMDBENCH_CONTROL_CCMD:-podman}
is_docker=$(which docker || true)
if [[ ! -z ${is_docker} ]]; then
  export LLMDBENCH_CONTROL_CCMD=docker
fi
if [[ $LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED -eq 0 && ! -f ~/.llmdbench_dependencies_checked ]]
then
  deplist="$LLMDBENCH_CONTROL_SCMD $LLMDBENCH_CONTROL_PCMD $LLMDBENCH_CONTROL_KCMD $LLMDBENCH_CONTROL_HCMD kubectl kustomize"
  echo "Checking dependencies \"$deplist\""
  for req in $deplist kubectl kustomize; do
    echo -n "Checking dependency \"${req}\"..."
    is_req=$(which ${req} || true)
    if [[ -z ${is_req} ]]; then
      echo "❌ Dependency \"${req}\" is missing"
      exit 1
    fi
    echo "done"
  done
  touch ~/.llmdbench_dependencies_checked
  export LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED=1
fi

if [[ $LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED -eq 0 ]]; then
  return 0
fi

if [[ ! -z $LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO ]]; then
  export LLMDBENCH_DEPLOY_SCENARIO=$LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO
fi

if [[ ! -z $LLMDBENCH_DEPLOY_SCENARIO ]]; then
  export LLMDBENCH_SCENARIO_FULL_PATH=$(echo ${LLMDBENCH_MAIN_DIR}/scenarios/$LLMDBENCH_DEPLOY_SCENARIO'.sh' | $LLMDBENCH_CONTROL_SCMD 's^.sh.sh^.sh^g')
  if [[ -f $LLMDBENCH_SCENARIO_FULL_PATH ]]; then
    source $LLMDBENCH_SCENARIO_FULL_PATH
  elif [[ $LLMDBENCH_SCENARIO_FULL_PATH == "${LLMDBENCH_MAIN_DIR}/scenarios/none.sh" ]]; then
    true
  else
    echo "❌ Scenario file \"$LLMDBENCH_SCENARIO_FULL_PATH\" could not be found."
    exit 1
  fi
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

export LLMDBENCH_CONTROL_WORK_DIR=${LLMDBENCH_CONTROL_WORK_DIR:-$(mktemp -d -t ${LLMDBENCH_CONTROL_CLUSTER_NAME}-$(echo $0 | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_CONTROL_SCMD -e 's^.sh^^g' -e 's^./^^g')XXX)}

mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls
mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands
mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/environment
mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/harnesses
mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles
mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/results

if [[ -f ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME} ]]; then
  export LLMDBENCH_CONTROL_KCMD="oc --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME}"
  export LLMDBENCH_CONTROL_HCMD="helm --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME}"
  cp -f ${HOME}/.kube/config-${LLMDBENCH_CONTROL_CLUSTER_NAME} $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx
  export LLMDBENCH_CONTROL_REMOTE_KUBECONFIG_FILENAME=config-${LLMDBENCH_CONTROL_CLUSTER_NAME}
elif [[ -z $LLMDBENCH_CLUSTER_URL || $LLMDBENCH_CLUSTER_URL == "auto" ]]; then
  current_context=$(${LLMDBENCH_CONTROL_KCMD} config view -o json | jq -r '."current-context"' || true)
  export LLMDBENCH_CONTROL_CLUSTER_NAME=$(echo $current_context | cut -d '/' -f 2 | cut -d '-' -f 2)
  if [[ $LLMDBENCH_CONTROL_WARNING_DISPLAYED -eq 0 ]]; then
    echo "WARNING: environment variable LLMDBENCH_CLUSTER_URL=$LLMDBENCH_CLUSTER_URL. Will attempt to use current context \"${current_context}\"."
    export LLMDBENCH_CONTROL_WARNING_DISPLAYED=1
    sleep 5
  fi
  ${LLMDBENCH_CONTROL_KCMD} config view --minify --flatten --raw --context=${current_context} > $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx
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
  is_ns=$($LLMDBENCH_CONTROL_KCMD get namespace | grep ${LLMDBENCH_VLLM_COMMON_NAMESPACE} || true)
  if [[ ! -z ${is_ns} ]]; then
    export LLMDBENCH_CONTROL_PROXY_UID=$($LLMDBENCH_CONTROL_KCMD get namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} -o json | jq -e -r '.metadata.annotations["openshift.io/sa.scc.uid-range"]' | perl -F'/' -lane 'print $F[0]+1');
  fi
fi

for mt in standalone deployer; do
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

function model_attribute {
  local model=$1
  local attribute=$2

  # Do not use associative arrays. Not supported by MacOS with older bash versions
#  declare -A LLMDBENCH_MODEL_ALIAS_TO_NAME
#  LLMDBENCH_MODEL_ALIAS_TO_NAME["llama-3b"]="meta-llama/Llama-3.2-3B-Instruct"
#  LLMDBENCH_MODEL_ALIAS_TO_NAME["llama-8b"]="meta-llama/Llama-3.1-8B-Instruct"
#  LLMDBENCH_MODEL_ALIAS_TO_NAME["llama-70b"]="meta-llama/Llama-3.1-70B-Instruct"
#  LLMDBENCH_MODEL_ALIAS_TO_NAME["llama-17b"]="RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic" #pragma: allowlist secret
#  is_alias=$(echo ${LLMDBENCH_MODEL_ALIAS_TO_NAME[${model}]} || true)
#  if [[ ! -z ${is_alias} ]]; then
#    local model=$is_alias
#  fi

  case "$model" in
    "llama-3b")
      local model=meta-llama/Llama-3.2-3B-Instruct ;;
    "llama-8b")
      local model=meta-llama/Llama-3.1-8B-Instruct ;;
    "llama-70b")
      local model=meta-llama/Llama-3.1-70B-Instruct ;;
    "llama-17b")
      local model=RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic ;;
    *)
      true ;;
  esac

  local modelcomponents=$(echo $model | cut -d '/' -f 2 | $LLMDBENCH_CONTROL_SCMD 's^-^\n^g' )
  local type=$(echo "${modelcomponents}" | grep -Ei "nstruct|hf")
  local parameters=$(echo "${modelcomponents}" | grep -Ei "^[0-9].*b")
  local majorversion=$(echo "${modelcomponents}" | grep -Ei "^[0-9]" | grep -Evi "b|E" | cut -d '.' -f 1)
  local kind=$(echo "${modelcomponents}" | head -n 1 | cut -d '/' -f 1)
  local label=${kind}-${majorversion}-${parameters}

  if [[ $attribute != "model" ]];
  then
    echo ${!attribute} | tr '[:upper:]' '[:lower:]'
  else
    echo ${!attribute}
  fi
}
export -f model_attribute


function llmdbench_execute_cmd {
  set +euo pipefail
  local actual_cmd=$1
  local dry_run=${2:-1}
  local verbose=${3:-0}
  local silent=${4:-1}
  local attempts=${5:-1}
  local counter=1
  local delay=10

  if [[ ${dry_run} -eq 1 ]]; then

    _msg="---> would have executed the command \"${actual_cmd}\""
    echo ${_msg}
    echo ${_msg} > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/$(date +%s%N)_command.log
    return 0
  else
    _msg="---> will execute the command \"${actual_cmd}\""
    echo ${_msg} > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/$(date +%s%N)_command.log
    while [[ "${counter}" -le "${attempts}" ]]; do
      if [[ ${verbose} -eq 0 && ${silent} -eq 1 ]]; then
        eval ${actual_cmd} &>/dev/null
        local ecode=$?
      elif [[ ${verbose} -eq 0 && ${silent} -eq 0 ]]; then
        eval ${actual_cmd}
        local ecode=$?
      else
        echo ${_msg}
        eval ${actual_cmd}
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
  fi

  set -euo pipefail
  return $ecode
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

function add_additional_env_to_yaml {
  local output="REPLACEFIRSTNEWLINE"
  for envvar in ${LLMDBENCH_VLLM_STANDALONE_ENVVARS_TO_YAML//,/ }; do
    output=$output"REPLACE_NEWLINEREPLACE_SPACESN- name: $(echo ${envvar} | $LLMDBENCH_CONTROL_SCMD -e 's^LLMDBENCH_VLLM_STANDALONE_^^g')REPLACE_NEWLINEREPLACE_SPACESVvalue: \"${!envvar}\""
  done
  echo -e ${output} | $LLMDBENCH_CONTROL_SCMD -e 's^REPLACEFIRSTNEWLINEREPLACE_NEWLINEREPLACE_SPACESN^^' -e 's^REPLACE_NEWLINE^\n^g' -e 's^REPLACE_SPACESN^        ^g'  -e 's^REPLACE_SPACESV^          ^g'  -e '/^*$/d'
}
export -f add_additional_env_to_yaml

function render_string {
  local string=$1
  local model=$2

  echo "s^REPLACE_MODEL^$(model_attribute $model model)^g" > $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  echo "s^____^ ^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  for entry in $(echo ${string} | $LLMDBENCH_CONTROL_SCMD -e 's/____/ /g' -e 's^-^\n^g' -e 's^:^\n^g' -e 's^ ^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq); do
    parameter_name=$(echo ${entry} | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_ENV_^\n______^g" -e "s^\"^^g" -e "s^'^^g" | grep "______" |  $LLMDBENCH_CONTROL_SCMD -e "s^______^^g")
    entry=REPLACE_ENV_${parameter_name}
    value=$(echo ${!parameter_name})
    echo "s^${entry}^${value}^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  done

  echo ${string} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
}
export -f render_string

function render_template {
  local template_file_path=$1
  local output_file_path=$2

  rm -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  for entry in $(cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -e 's^-^\n^g' -e 's^:^\n^g' -e 's^ ^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq); do
    parameter_name=$(echo ${entry} | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_ENV_^\n______^g" -e "s^\"^^g" -e "s^'^^g" | grep "______" |  $LLMDBENCH_CONTROL_SCMD -e "s^______^^g")
    entry=REPLACE_ENV_${parameter_name}
    value=$(echo ${!parameter_name})
    echo "s^${entry}^${value}^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  done
  cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands > $output_file_path
}
export -f render_template

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