# Shared configuration and validation

# Cluster access
export LLMDBENCH_CLUSTER_URL="${LLMDBENCH_CLUSTER_URL:-auto}"
export LLMDBENCH_CLUSTER_TOKEN="${LLMDBENCH_CLUSTER_TOKEN:-sha256~sVYh-xxx}"
export LLMDBENCH_CLUSTER_NAMESPACE="${LLMDBENCH_CLUSTER_NAMESPACE:-}"
export LLMDBENCH_CLUSTER_SERVICE_ACCOUNT="${LLMDBENCH_CLUSTER_SERVICE_ACCOUNT:-default}"

# Secrets
export LLMDBENCH_HF_TOKEN="${LLMDBENCH_HF_TOKEN:-}"
export LLMDBENCH_QUAY_USER="${LLMDBENCH_QUAY_USER:-}"
export LLMDBENCH_QUAY_PASSWORD="${LLMDBENCH_QUAY_PASSWORD:-}"
export LLMDBENCH_DOCKER_EMAIL="${LLMDBENCH_DOCKER_EMAIL:-your@email.address}"

# External repositories
export LLMDBENCH_FMPERF_GIT_REPO="${LLMDBENCH_FMPERF_GIT_REPO:-https://github.com/wangchen615/fmperf.git}"
export LLMDBENCH_FMPERF_DIR="${LLMDBENCH_FMPERF_DIR:-/tmp}"
export LLMDBENCH_FMPERF_GIT_BRANCH="${LLMDBENCH_FMPERF_GIT_BRANCH:-dev-lmbenchmark}"
export LLMDBENCH_KVCM_DIR="${LLMDBENCH_KVCM_DIR:-/tmp}"
export LLMDBENCH_KVCM_GIT_BRANCH=${LLMDBENCH_KVCM_GIT_BRANCH:-dev}
export LLMDBENCH_GAIE_DIR="${LLMDBENCH_GAIE_DIR:-/tmp}"

# Applicable to both standalone and p2p
export LLMDBENCH_VLLM_COMMON_AFFINITY=${LLMDBENCH_VLLM_COMMON_AFFINITY:-NVIDIA-A100-SXM4-80GB}
export LLMDBENCH_VLLM_COMMON_REPLICAS=${LLMDBENCH_VLLM_COMMON_REPLICAS:-1}
export LLMDBENCH_VLLM_COMMON_PERSISTENCE_ENABLED=${LLMDBENCH_VLLM_COMMON_PERSISTENCE_ENABLED:-true}
export LLMDBENCH_VLLM_COMMON_GPU_NR=${LLMDBENCH_VLLM_COMMON_GPU_NR:-1}
export LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL=${LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL:-0.95}
export LLMDBENCH_VLLM_COMMON_CPU_NR=${LLMDBENCH_VLLM_COMMON_CPU_NR:-4}
export LLMDBENCH_VLLM_COMMON_CPU_MEM=${LLMDBENCH_VLLM_COMMON_CPU_MEM:-40Gi}
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=${LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN:-16384}
export LLMDBENCH_VLLM_COMMON_PVC_NAME=${LLMDBENCH_VLLM_COMMON_PVC_NAME:-""}
export LLMDBENCH_VLLM_COMMON_PVC_MOUNTPOINT=${LLMDBENCH_VLLM_COMMON_PVC_MOUNTPOINT:-/data}
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS="${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS:-ocs-storagecluster-cephfs}"
export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE="${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE:-300Gi}"
export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME:-"vllm-common-hf-token"}
export LLMDBENCH_VLLM_COMMON_PULL_SECRET_NAME=${LLMDBENCH_VLLM_COMMON_PULL_SECRET_NAME:-"vllm-common-quay-secret"}

# Standalone-specific parameters
export LLMDBENCH_VLLM_STANDALONE_IMAGE=${LLMDBENCH_VLLM_STANDALONE_IMAGE:-"vllm/vllm-openai:latest"}
export LLMDBENCH_VLLM_STANDALONE_HTTPROUTE=${LLMDBENCH_VLLM_STANDALONE_HTTPROUTE:-0}

# P2P-specific parameters
export LLMDBENCH_VLLM_P2P_LMCACHE_MAX_LOCAL_CPU_SIZE=${LLMDBENCH_VLLM_P2P_LMCACHE_MAX_LOCAL_CPU_SIZE:-40}
export LLMDBENCH_VLLM_P2P_IMAGE_REPOSITORY=${LLMDBENCH_VLLM_P2P_IMAGE_REPOSITORY:-quay.io/llm-d/llm-d-dev}
export LLMDBENCH_VLLM_P2P_IMAGE_TAG=${LLMDBENCH_VLLM_P2P_IMAGE_TAG:-lmcache-0.0.6-amd64}

# Endpoint Picker Parameters
export LLMDBENCH_EPP_IMAGE=${LLMDBENCH_EPP_IMAGE:-quay.io/llm-d/llm-d-gateway-api-inference-extension-dev:0.0.5-amd64}
export LLMDBENCH_EPP_ENABLE_PREFIX_AWARE_SCORER=${LLMDBENCH_EPP_ENABLE_PREFIX_AWARE_SCORER:-true}
export LLMDBENCH_EPP_PREFIX_AWARE_SCORER_WEIGHT=${LLMDBENCH_EPP_PREFIX_AWARE_SCORER_WEIGHT:-1.0}
export LLMDBENCH_EPP_ENABLE_KVCACHE_AWARE_SCORER=${LLMDBENCH_EPP_ENABLE_KVCACHE_AWARE_SCORER:-true}
export LLMDBENCH_EPP_KVCACHE_AWARE_SCORER_WEIGHT=${LLMDBENCH_EPP_KVCACHE_AWARE_SCORER_WEIGHT:-2.0}
export LLMDBENCH_EPP_ENABLE_LOAD_AWARE_SCORER=${LLMDBENCH_EPP_ENABLE_LOAD_AWARE_SCORER:-false}
export LLMDBENCH_EPP_LOAD_AWARE_SCORER_WEIGHT=${LLMDBENCH_EPP_LOAD_AWARE_SCORER_WEIGHT:-1.0}
export LLMDBENCH_EPP_PD_ENABLE=${LLMDBENCH_EPP_PD_ENABLE:-false}

# Not sure if those should be set
export LLMDBENCH_IGW_REDIS_PORT="${LLMDBENCH_IGW_REDIS_PORT:-8100}"

# Experiments
export LLMDBENCH_FMPERF_CONDA_ENV_NAME="${LLMDBENCH_FMPERF_CONDA_ENV_NAME:-fmperf-env}"
export LLMDBENCH_FMPERF_EXPERIMENT_HARNESS="${LLMDBENCH_FMPERF_EXPERIMENT_HARNESS:-llm-d-benchmark.py}"
export LLMDBENCH_FMPERF_EXPERIMENT_PROFILE="${LLMDBENCH_FMPERF_EXPERIMENT_PROFILE:-sanity_short_input.yaml}"
export LLMDBENCH_FMPERF_PVC_NAME="${LLMDBENCH_FMPERF_PVC_NAME:-"workload-pvc"}"
export LLMDBENCH_FMPERF_PVC_SIZE="${LLMDBENCH_FMPERF_PVC_SIZE:-20Gi}"
export LLMDBENCH_FMPERF_CONTAINER_IMAGE=${LLMDBENCH_FMPERF_CONTAINER_IMAGE:-lmcache/lmcache-benchmark:main}

# LLM-D-Benchmark deployment specific variables
export LLMDBENCH_DEPLOY_MODEL_LIST=${LLMDBENCH_DEPLOY_MODEL_LIST:-"llama-8b"}
export LLMDBENCH_DEPLOY_METHODS=${LLMDBENCH_DEPLOY_METHODS:-"standalone"}

# Control variables
export LLMDBENCH_CONTROL_CLUSTER_NAME=${LLMDBENCH_CONTROL_CLUSTER_NAME:-$(echo ${LLMDBENCH_CLUSTER_URL} | cut -d '.' -f 2)}
export LLMDBENCH_CONTROL_ENVVAR_DISPLAYED=${LLMDBENCH_CONTROL_ENVVAR_DISPLAYED:-0}
export LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED=${LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED:-0}
export LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED=${LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED:-0}
export LLMDBENCH_CONTROL_PERMISSIONS_CHECKED=${LLMDBENCH_CONTROL_PERMISSIONS_CHECKED:-0}
export LLMDBENCH_CONTROL_WARNING_DISPLAYED=${LLMDBENCH_CONTROL_WARNING_DISPLAYED:-0}
export LLMDBENCH_CONTROL_WAIT_TIMEOUT=${LLMDBENCH_CONTROL_WAIT_TIMEOUT:-900}
export LLMDBENCH_CONTROL_RESOURCE_LIST=deployment,httproute,route,service,gateway,gatewayparameters,inferencepool,inferencemodel,cm,ing,pod,secret
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

if [[ $LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED -eq 0 && ! -f ~/.llmdbench_dependencies_checked ]]
then
  for req in $LLMDBENCH_CONTROL_SCMD $LLMDBENCH_CONTROL_PCMD $LLMDBENCH_CONTROL_KCMD $LLMDBENCH_CONTROL_HCMD kubectl kustomize; do
    echo -n "Checking dependency \"${req}\"..."
    is_req=$(which ${req} || true)
    if [[ -z ${is_req} ]]; then
      echo "Dependency \"${req}\" is missing"
      exit 1
    fi
    echo "done"
  done
  echo -n "Checking if your current bash (version $(printf "%s\n" $BASH_VERSION) support arrays..."
  declare -A test
  echo done
  touch ~/.llmdbench_dependencies_checked
  export LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED=1
fi

if [[ $LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED -eq 0 ]]; then
  return 0
fi

if [[ ! -z $LLMDBENCH_DEPLOY_SCENARIO ]]; then
  export LLMDBENCH_SCENARIO_FULL_PATH=$(echo ${LLMDBENCH_CONTROL_DIR}/scenarios/$LLMDBENCH_DEPLOY_SCENARIO'.sh' | $LLMDBENCH_CONTROL_SCMD 's^.sh.sh^.sh^g')
  if [[ -f $LLMDBENCH_SCENARIO_FULL_PATH ]]; then
    source $LLMDBENCH_SCENARIO_FULL_PATH
  fi
fi

overridevarlist=$(env | grep _CLIOVERRIDE_ | cut -d '=' -f 1)
if [[ ! -z $overridevarlist ]]; then
  for overridevar in $overridevarlist; do
    actualvar=$(echo $overridevar | $LLMDBENCH_CONTROL_SCMD 's^_CLIOVERRIDE^^g')
    if [[ $LLMDBENCH_CONTROL_VERBOSE -eq 1 && $LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED -eq 0 ]]; then
      echo "Environment variable $actualvar was overriden by command line options"
    fi
    export $actualvar=${!overridevar}
  done
  export LLMDBENCH_CONTROL_OVERRIDE_COMMAND_DISPLAYED=1
fi

required_vars=("LLMDBENCH_CLUSTER_NAMESPACE")
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
elif [[ -z $LLMDBENCH_CLUSTER_URL || $LLMDBENCH_CLUSTER_URL == "auto" ]]; then
  current_context=$(${LLMDBENCH_CONTROL_KCMD} config view -o json | jq -r '."current-context"' || true)
  export LLMDBENCH_CONTROL_CLUSTER_NAME=$(echo $current_context | cut -d '/' -f 2 | cut -d '-' -f 2)
  if [[ $LLMDBENCH_CONTROL_WARNING_DISPLAYED -eq 0 ]]; then
    echo "WARNING: environment variable LLMDBENCH_CLUSTER_URL=$LLMDBENCH_CLUSTER_URL. Will attempt to use current context \"${current_context}\"."
    LLMDBENCH_CONTROL_WARNING_DISPLAYED=1
    sleep 5
  fi
  ${LLMDBENCH_CONTROL_KCMD} config view --minify --flatten --raw --context=${current_context} > $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx
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

  if [[ $current_namespace != $LLMDBENCH_CLUSTER_NAMESPACE ]]; then
    namespace_exists=$(${LLMDBENCH_CONTROL_KCMD} get namespaces | grep $LLMDBENCH_CLUSTER_NAMESPACE || true)
    if [[ ! -z $namespace_exists ]]; then
      ${LLMDBENCH_CONTROL_KCMD} project $LLMDBENCH_CLUSTER_NAMESPACE
    fi
  fi
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
  is_ns=$($LLMDBENCH_CONTROL_KCMD get namespace | grep ${LLMDBENCH_CLUSTER_NAMESPACE} || true)
  if [[ ! -z ${is_ns} ]]; then
    export LLMDBENCH_CONTROL_PROXY_UID=$($LLMDBENCH_CONTROL_KCMD get namespace ${LLMDBENCH_CLUSTER_NAMESPACE} -o json | jq -e -r '.metadata.annotations["openshift.io/sa.scc.uid-range"]' | perl -F'/' -lane 'print $F[0]+1');
  fi
fi

for mt in standalone p2p; do
  is_env=$(echo $LLMDBENCH_DEPLOY_METHODS | grep $mt || true)
  if [[ -z $is_env ]]; then
    export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_$(echo $mt | tr '[:lower:]' '[:upper:]')_ACTIVE=0
  else
    export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_$(echo $mt | tr '[:lower:]' '[:upper:]')_ACTIVE=1
  fi
done

if [[ $LLMDBENCH_CONTROL_PERMISSIONS_CHECKED -eq 0 ]]; then
  for resource in namespace ${LLMDBENCH_CONTROL_RESOURCE_LIST//,/ }; do
    ra=$($LLMDBENCH_CONTROL_KCMD --namespace $LLMDBENCH_CLUSTER_NAMESPACE auth can-i '*' $resource 2>&1 | grep yes || true)
    if [[ -z ${ra} ]]
    then
      echo "ERROR: the current user cannot operate over the resource \"${resource}\""
      exit 1
    fi

    ra=$($LLMDBENCH_CONTROL_KCMD --namespace $LLMDBENCH_CLUSTER_NAMESPACE auth can-i patch serviceaccount 2>&1 | grep yes || true)
    if [[ -z ${ra} ]]
    then
      echo "ERROR: the current user cannot operate patch serviceaccount\""
      exit 1
    fi
    export LLMDBENCH_CONTROL_PERMISSIONS_CHECKED=1
  done
fi

export LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL=${SHELL:5}

declare -A LLMDBENCH_MODEL2PARAM
#LLMDBENCH_MODEL2PARAM["llama-8b:label"]="llama-2-8b"
#LLMDBENCH_MODEL2PARAM["llama-8b:name"]="meta-llama/Llama-2-8b-chat-hf"
#---
LLMDBENCH_MODEL2PARAM["llama-8b:label"]="llama-3-8b"
LLMDBENCH_MODEL2PARAM["llama-8b:name"]="meta-llama/Llama-3.1-8B-Instruct"
LLMDBENCH_MODEL2PARAM["llama-8b:type"]="instruct"
LLMDBENCH_MODEL2PARAM["llama-8b:params"]="8b"
LLMDBENCH_MODEL2PARAM["llama-8b:cmdline"]="vllm serve meta-llama/Llama-3.1-8B-Instruct --port 80 --disable-log-requests --gpu-memory-utilization $LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL"
#---
LLMDBENCH_MODEL2PARAM["llama-70b:label"]="llama-3-70b"
LLMDBENCH_MODEL2PARAM["llama-70b:name"]="meta-llama/Llama-3.1-70B-Instruct"
LLMDBENCH_MODEL2PARAM["llama-70b:type"]="instruct"
LLMDBENCH_MODEL2PARAM["llama-70b:params"]="70b"
LLMDBENCH_MODEL2PARAM["llama-70b:cmdline"]="vllm serve meta-llama/Llama-3.1-70B-Instruct --port 80 --max-model-len ${LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN} --disable-log-requests --gpu-memory-utilization $LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL --tensor-parallel-size $LLMDBENCH_VLLM_COMMON_GPU_NR"
#---
LLMDBENCH_MODEL2PARAM["llama-17b:label"]="llama-4-17b"
LLMDBENCH_MODEL2PARAM["llama-17b:name"]="RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic"
LLMDBENCH_MODEL2PARAM["llama-17b:type"]="scout"
LLMDBENCH_MODEL2PARAM["llama-17b:params"]="17b"
LLMDBENCH_MODEL2PARAM["llama-17b:cmdline"]="vllm serve RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic --port 80 --max-model-len ${LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN} --disable-log-requests --gpu-memory-utilization $LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL --tensor-parallel-size $LLMDBENCH_VLLM_COMMON_GPU_NR"

if [[ -z $LLMDBENCH_VLLM_COMMON_PVC_NAME ]]; then
  LLMDBENCH_MODEL2PARAM["llama-70b:pvc"]="vllm-standalone-llama-70b-cache"
  LLMDBENCH_MODEL2PARAM["llama-8b:pvc"]="vllm-standalone-llama-8b-cache"
  LLMDBENCH_MODEL2PARAM["llama-17b:pvc"]="vllm-standalone-llama-17b-cache"
else
  LLMDBENCH_MODEL2PARAM["llama-70b:pvc"]="$LLMDBENCH_VLLM_COMMON_PVC_NAME"
  LLMDBENCH_MODEL2PARAM["llama-8b:pvc"]="$LLMDBENCH_VLLM_COMMON_PVC_NAME"
  LLMDBENCH_MODEL2PARAM["llama-17b:pvc"]="$LLMDBENCH_VLLM_COMMON_PVC_NAME"
fi

function llmdbench_execute_cmd {
  set +euo pipefail
  local actual_cmd=$1
  local dry_run=${2:-1}
  local verbose=${3:-0}
  local attempts=${4:-1}
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
      if [[ ${verbose} -eq 0 ]]; then
        eval ${actual_cmd} &>/dev/null
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