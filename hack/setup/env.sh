# Shared configuration and validation

# Cluster access
export LLMDBENCH_OPENSHIFT_HOST="${LLMDBENCH_OPENSHIFT_HOST:-auto}"
export LLMDBENCH_OPENSHIFT_TOKEN="${LLMDBENCH_OPENSHIFT_TOKEN:-sha256~sVYh-xxx}"
export LLMDBENCH_OPENSHIFT_NAMESPACE="${LLMDBENCH_OPENSHIFT_NAMESPACE:-}"
export LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT="${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT:-default}"

# Secrets
export LLMDBENCH_HF_TOKEN="${LLMDBENCH_HF_TOKEN:-}"
export LLMDBENCH_QUAY_USER="${LLMDBENCH_QUAY_USER:-}"
export LLMDBENCH_QUAY_PASSWORD="${LLMDBENCH_QUAY_PASSWORD:-}"
export LLMDBENCH_DOCKER_EMAIL="${LLMDBENCH_DOCKER_EMAIL:-your@email.address}"

export LLMDBENCH_STORAGE_CLASS="${LLMDBENCH_STORAGE_CLASS:-ocs-storagecluster-cephfs}"

# External repositories
export LLMDBENCH_FMPERF_GIT_REPO="${LLMDBENCH_FMPERF_GIT_REPO:-https://github.com/wangchen615/fmperf.git}"
export LLMDBENCH_FMPERF_DIR="${LLMDBENCH_FMPERF_DIR:-/tmp}"
export LLMDBENCH_FMPERF_GIT_BRANCH="${LLMDBENCH_FMPERF_GIT_BRANCH:-dev-lmbenchmark}"
export LLMDBENCH_KVCM_DIR="${LLMDBENCH_KVCM_DIR:-/tmp}"
export LLMDBENCH_KVCM_GIT_BRANCH=${LLMDBENCH_KVCM_GIT_BRANCH:-dev}
export LLMDBENCH_GAIE_DIR="${LLMDBENCH_GAIE_DIR:-/tmp}"

# Directly affects helm charts
export LLMDBENCH_GPU_MODEL=${LLMDBENCH_GPU_MODEL:-NVIDIA-A100-SXM4-80GB}
export LLMDBENCH_VLLM_REPLICAS=${LLMDBENCH_VLLM_REPLICAS:-1}
export LLMDBENCH_VLLM_PERSISTENCE_ENABLED=${LLMDBENCH_VLLM_PERSISTENCE_ENABLED:-false}
export LLMDBENCH_VLLM_GPU_NR=${LLMDBENCH_VLLM_GPU_NR:-2}
export LLMDBENCH_VLLM_GPU_MEM_UTIL=${LLMDBENCH_VLLM_GPU_MEM_UTIL:-0.95}
export LLMDBENCH_VLLM_MAX_MODEL_LEN=${LLMDBENCH_VLLM_MAX_MODEL_LEN:-16384}
export LLMDBENCH_VLLM_IMAGE_REPOSITORY=${LLMDBENCH_VLLM_IMAGE_REPOSITORY:-quay.io/llm-d/llm-d-dev}
export LLMDBENCH_VLLM_IMAGE_TAG=${LLMDBENCH_VLLM_IMAGE_TAG:-lmcache-0.0.6-amd64}

# Size of PVC (vllm-standalone)
export LLMDBENCH_MODEL_CACHE_SIZE="${LLMDBENCH_MODEL_CACHE_SIZE:-300Gi}"

# Endpoint Picker Parameters
export LLMDBENCH_EPP_IMAGE=${LLMDBENCH_EPP_IMAGE:-quay.io/llm-d/llm-d-gateway-api-inference-extension-dev:0.0.5-amd64}
export LLMDBENCH_EPP_ENABLE_PREFIX_AWARE_SCORER=${LLMDBENCH_EPP_ENABLE_PREFIX_AWARE_SCORER:-true}
export LLMDBENCH_EPP_PREFIX_AWARE_SCORER_WEIGHT=${LLMDBENCH_EPP_PREFIX_AWARE_SCORER_WEIGHT:-1.0}
export LLMDBENCH_EPP_ENABLE_KVCACHE_AWARE_SCORER=${LLMDBENCH_EPP_ENABLE_KVCACHE_AWARE_SCORER:-true}
export LLMDBENCH_EPP_KVCACHE_AWARE_SCORER_WEIGHT=${LLMDBENCH_EPP_KVCACHE_AWARE_SCORER_WEIGHT:-2.0}
export LLMDBENCH_EPP_ENABLE_LOAD_AWARE_SCORER=${LLMDBENCH_EPP_ENABLE_LOAD_AWARE_SCORER:-false}
export LLMDBENCH_EPP_LOAD_AWARE_SCORER_WEIGHT=${LLMDBENCH_EPP_LOAD_AWARE_SCORER_WEIGHT:-1.0}
export LLMDBENCH_EPP_PD_ENABLE=${LLMDBENCH_EPP_PD_ENABLE:-false}

# Hopefully will be replaced soon
export OPENSHIFT_NAMESPACE=${LLMDBENCH_OPENSHIFT_NAMESPACE}
export OPENSHIFT_TOKEN=${LLMDBENCH_OPENSHIFT_TOKEN}

# Not sure if those should be set
export LLMDBENCH_REDIS_PORT="${LLMDBENCH_REDIS_PORT:-8100}"

export LLMDBENCH_MODEL_IMAGE=${LLMDBENCH_MODEL_IMAGE:-"vllm/vllm-openai:latest"}

# Experiments
export LLMDBENCH_CONDA_ENV_NAME="${LLMDBENCH_CONDA_ENV_NAME:-fmperf-env}"
export LLMDBENCH_FMPERF_EXPERIMENT_LIST="${LLMDBENCH_FMPERF_EXPERIMENT_LIST:-examples/example_llm-d-lmbenchmark-openshift.py}"

# LLM-D-Benchmark deployment specific variables
export LLMDBENCH_MODEL_LIST=${LLMDBENCH_MODEL_LIST:-"llama-8b,llama-70b"}
export LLMDBENCH_ENVIRONMENT_TYPES=${LLMDBENCH_ENVIRONMENT_TYPES:-"standalone,vllm"}

# Control variables
export LLMDBENCH_OPENSHIFT_CLUSTER_NAME=$(echo ${LLMDBENCH_OPENSHIFT_HOST} | cut -d '.' -f 2)
export LLMDBENCH_DEPENDENCIES_CHECKED=${LLMDBENCH_DEPENDENCIES_CHECKED:-0}
export LLMDBENCH_WARNING_DISPLAYED=${LLMDBENCH_WARNING_DISPLAYED:-0}
export LLMDBENCH_WAIT_TIMEOUT=${LLMDBENCH_WAIT_TIMEOUT:-900}
export LLMDBENCH_RESOURCE_LIST=deployment,httproute,route,service,gateway,gatewayparameters,inferencepool,inferencemodel,cm,ing,pod,secret
export LLMDBENCH_KCMD=oc
export LLMDBENCH_HCMD=helm

required_vars=("LLMDBENCH_OPENSHIFT_NAMESPACE" "LLMDBENCH_HF_TOKEN" "LLMDBENCH_QUAY_USER" "LLMDBENCH_QUAY_PASSWORD")
for var in "${required_vars[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "❌ Environment variable '$var' is not set."
    exit 1
  fi
done

uname -s | grep -qi darwin
if [[ $? -eq 0 ]]
then
    export LLMDBENCH_HOST_OS=mac
  which gsed > /dev/null 2>&1
  if [[ $? -ne 0 ]]
  then
    brew install gnu-sed
  fi
  export LLMDBENCH_SCMD=gsed
else
    export LLMDBENCH_HOST_OS=linux
    export LLMDBENCH_SCMD=sed
fi

export LLMDBENCH_PCMD=${LLMDBENCH_PCMD:-python3}

if [[ $LLMDBENCH_DEPENDENCIES_CHECKED -eq 0 ]]
then
  for req in $LLMDBENCH_SCMD $LLMDBENCH_PCMD $LLMDBENCH_KCMD $LLMDBENCH_HCMD kubectl kustomize; do
    is_req=$(which ${req} || true)
    if [[ -z ${is_req} ]]; then
      echo "Dependency \"${req}\" is missing"
      exit 1
    fi
  done
  export LLMDBENCH_DEPENDENCIES_CHECKED=1
fi

if [[ -f ${HOME}/.kube/config-${LLMDBENCH_OPENSHIFT_CLUSTER_NAME} ]]; then
  export LLMDBENCH_KCMD="oc --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_OPENSHIFT_CLUSTER_NAME}"
  export LLMDBENCH_HCMD="helm --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_OPENSHIFT_CLUSTER_NAME}"
elif [[ -z $LLMDBENCH_OPENSHIFT_HOST || $LLMDBENCH_OPENSHIFT_HOST == "auto" ]]; then
  current_context=$(${LLMDBENCH_KCMD} config view -o json | jq -r '."current-context"' || true)
  if [[ $LLMDBENCH_WARNING_DISPLAYED -eq 0 ]]; then
    echo "WARNING: environment variable LLMDBENCH_OPENSHIFT_HOST=$LLMDBENCH_OPENSHIFT_HOST. Will attempt to use current context \"${current_context}\"."
    LLMDBENCH_WARNING_DISPLAYED=1
  fi
  sleep 5
else
  current_context=$(${LLMDBENCH_KCMD} config view -o json | jq -r '."current-context"' || true)
  current_namespace=$(echo $current_context | cut -d '/' -f 1)
  current_url=$(echo $current_context | cut -d '/' -f 2 | cut -d ':' -f 1 | $LLMDBENCH_SCMD "s^-^.^g")
  target_url=$(echo $LLMDBENCH_OPENSHIFT_HOST | cut -d '/' -f 3 | $LLMDBENCH_SCMD "s^-^.^g")
  if [[ $current_url != $target_url ]]; then
    ${LLMDBENCH_KCMD} login --token="${LLMDBENCH_OPENSHIFT_TOKEN}" --server="${LLMDBENCH_OPENSHIFT_HOST}:6443"
  fi

  if [[ $current_namespace != $LLMDBENCH_OPENSHIFT_NAMESPACE ]]; then
    ${LLMDBENCH_KCMD} project $LLMDBENCH_OPENSHIFT_NAMESPACE
  fi
fi

is_ns=$($LLMDBENCH_KCMD get namespace | grep ${LLMDBENCH_OPENSHIFT_NAMESPACE} || true)
if [[ ! -z ${is_ns} ]]; then
  export LLMDBENCH_PROXY_UID=$($LLMDBENCH_KCMD get namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} -o json | jq -e -r '.metadata.annotations["openshift.io/sa.scc.uid-range"]' | perl -F'/' -lane 'print $F[0]+1');
fi

export LLMDBENCH_IS_OPENSHIFT=0
is_ocp=$($LLMDBENCH_KCMD api-resources 2>&1 | grep 'route.openshift.io' || true)
if [[ ! -z ${is_ocp} ]] then
  export LLMDBENCH_IS_OPENSHIFT=1
else
  export LLMDBENCH_KCMD=$(echo $LLMDBENCH_KCMD | $LLMDBENCH_SCMD 's^oc ^kubectl^g')
fi

export LLMDBENCH_USER_IS_ADMIN=1
not_admin=$($LLMDBENCH_KCMD get crds 2>&1 | grep -i Forbidden || true)
if [[ ! -z ${not_admin} ]]; then
  export LLMDBENCH_USER_IS_ADMIN=0
fi

for mt in standalone p2p; do
  is_env=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep $mt || true)
  if [[ -z $is_env ]]; then
    export LLMDBENCH_ENVIRONMENT_TYPE_$(echo $mt | tr '[:lower:]' '[:upper:]')_ACTIVE=1
  else
    export LLMDBENCH_ENVIRONMENT_TYPE_$(echo $mt | tr '[:lower:]' '[:upper:]')_ACTIVE=1
  fi
done

export LLMDBENCH_HOST_SHELL=${SHELL:5}

declare -A LLMDBENCH_MODEL2PARAM
#LLMDBENCH_MODEL2PARAM["llama-8b:label"]="llama-2-8b"
#LLMDBENCH_MODEL2PARAM["llama-8b:name"]="meta-llama/Llama-2-8b-chat-hf"
LLMDBENCH_MODEL2PARAM["llama-8b:label"]="llama-3-8b"
LLMDBENCH_MODEL2PARAM["llama-8b:name"]="meta-llama/Llama-3.1-8B-Instruct"
LLMDBENCH_MODEL2PARAM["llama-8b:params"]="8b"
LLMDBENCH_MODEL2PARAM["llama-70b:label"]="llama-3-70b"
LLMDBENCH_MODEL2PARAM["llama-70b:name"]="meta-llama/Llama-3.1-70B-Instruct"
LLMDBENCH_MODEL2PARAM["llama-70b:params"]="70b"

export LLMDBENCH_WORK_DIR=${LLMDBENCH_WORK_DIR:-$(mktemp -d -t ${LLMDBENCH_OPENSHIFT_CLUSTER_NAME}-$(echo $0 | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_SCMD -e 's^.sh^^g' -e 's^./^^g')XXX)}
mkdir -p ${LLMDBENCH_WORK_DIR}/yamls
mkdir -p ${LLMDBENCH_WORK_DIR}/commands
mkdir -p ${LLMDBENCH_WORK_DIR}/environment

function llmdbench_execute_cmd {
  set +euo pipefail
  local actual_cmd=$1
  local dry_run=${2:-1}
  local verbose=${3:-0}

  if [[ ${dry_run} -eq 1 ]]; then
    echo "---> would have executed the command \"${actual_cmd}\""
    return 0
  else
    if [[ ${verbose} -eq 0 ]]; then
      eval ${actual_cmd} &>/dev/null
    else
      echo "---> will execute the command \"${actual_cmd}\""
      eval ${actual_cmd}
    fi
    local ecode=$?
  fi

  set -euo pipefail
  return $ecode
}
export -f llmdbench_execute_cmd

function announce {
    # 1 - MESSAGE
    # 2 - LOGFILE
    local message=$(echo "${1}" | tr '\n' ' ' | $LLMDBENCH_SCMD "s/\t\t*/ /g")
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