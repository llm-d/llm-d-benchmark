#!/usr/bin/env bash

exec 3>&2   # fd for prompts

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "This script should be executed not sourced" >&3
  return 1
fi

function usage() {
  cat - <<EOF <(sed -n -e '/^vars/,/^)$/p' <${BASH_SOURCE[0]})
Usage:
  ${BASH_SOURCE[0]} [--clean] [--help]

Output: 
  Script to setup environment variables for run.sh

Example:
  \$> ${BASH_SOURCE[0]} > ~/myenv.sh
  \$> run.sh -c \$(realpath ~/myenv.sh) -w sanity_random

Option:
  Use ${BASH_SOURCE[0]} --clean to unset the llm-d-benchmark env varaibles if already set

EOF
}

error_exit() {  # RC message
  if [[ ! -z "${2}" ]]; then
    echo "$2" >&3
  fi
  exit $1
}

clean() {
  echo "Resetting benchmark variables"
  for var in "${vars[@]}"; do
    unset $var
  done
}

read_var() { # read_var prompt_msg default_value; error if empty reply
  echo >&3
  echo "$1 ${2:+(default: $2)}" >&3
  read -p "> " input
  reply="${input:-$2}"
  if [[ -z "${reply}" ]]; then
    return 1
  fi
  echo "$reply"
}

choose_var() { # choose_var [-o] prompt_msg choices; error if reply empty or "other" (with -o)
  if [[ "$1" == "-o" ]]; then
    other=true
    shift
  else
    other=false
  fi
  prompt="$1"
  shift
  if (($# == 0)); then
    return 1
  fi
  if [[ "${other}" == true ]]; then
    set -- "$@" "other"
  fi
  echo >&3
  echo "${prompt}" >&3
  export COLUMNS=1
  select input in "$@"; do
    [[ ! -z "$input" ]] && break
  done
  if [[ "other" == "${input}" ]] || [[ -z "${input}" ]]; then
    return 1
  fi
  echo "${input}"
}

export_var() {  # export_var name value
  if [[ "$1" == "-l" ]]; then   # allow list assignment; otherwise, value is first arg only
    name="$2"
    shift 2
    value="$@"
  else
    name="$1"
    value="$2"
  fi

 printf -v "${name}" "%s" "${value}"
 export "${name}"
 echo ">>>  ${name}=${!name}" >&3
}

vars=(
  LLMDBENCH_VLLM_COMMON_NAMESPACE   # [-p] namespace where llm-d stack is deployed
  LLMDBENCH_VLLM_HARNESS_NAMESPACE  # namespace where harness will be run (same as llm-d stack)
  HF_TOKEN_NAME                     # secret name when HF_TOKEN is stored in llm-d namespace
  LLMDBENCH_HF_TOKEN                # HuggingFace token (default to HF_TOKEN or token secret in llm-d stack
  LLMDBENCH_HARNESS_DIR             # directory for git clone of harness
  LLMDBENCH_INFRA_DIR               # directory for git clone of llm-d-infra
  LLMDBENCH_CONTROL_WORK_DIR        # directory for saving benchmark results
  LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS # Storage class for created PVCs
  LLMDBENCH_HARNESS_PVC_NAME        # [-k] PVC for benchmark results
  LLMDBENCH_HARNESS_NAME            # [-l] harness to use for benchmarking
  LLMDBENCH_DEPLOY_METHODS          # [-t] endpoint name (service / vLLM)
  LLMDBENCH_DEPLOY_MODEL_LIST       # [-m] model being benchmarked
  LLMDBENCH_HARNESS_WAIT_TIMEOUT    # [-s] how long to wait for results
)

while (($# > 0 )); do
  case $1 in
    -h|--help)
      usage
      error_exit 0
      ;;
    --clean)
      clean
      error_exit 0
      ;;
    *)
      error_exit 1 "Unknown argument $1. ${BASH_SOURCE[0]} -h for help."
      ;;
  esac
done

if [ ! -f ~/.llmdbench_dependencies_checked ]; then
  error_exit 1 "Please install all dependencies by running setup/install_deps.sh"
fi

k=$(type -p oc || type -p kubectl || 
  error_exit 1 "Please install at least one of kubectl or oc. You should run setup/install_deps.sh")

if ! $k auth whoami > /dev/null; then
  error_exit 1 "Please log in to your llm-d cluster"
fi


# Namespace
# =========
ns=${LLMDBENCH_VLLM_COMMON_NAMESPACE:-$($k config current-context | awk -F / '{print $1}')}
prompt="Please specify target namespace"
export_var LLMDBENCH_VLLM_COMMON_NAMESPACE $(read_var "${prompt}" "${ns}") ||
  error_exit 1 "LMDBENCH_VLLM_COMMON_NAMESPACE cannot be empty. Please make sure you are logged in to the llm-d cluster"
export_var LLMDBENCH_VLLM_HARNESS_NAMESPACE ${LLMDBENCH_VLLM_COMMON_NAMESPACE}


# HF TOKEN
# ========
hf_name="${HF_TOKEN_NAME:-llm-d-hf-token}"
prompt="HF token is needed for gated models. It is stored as a secret in the namespace.
Please confirm secret name for HF token"
export_var HF_TOKEN_NAME $(read_var "${prompt}" "${hf_name}") ||
  error_exit 1 "HF_TOKEN_NAME secret name cannot be empty. Please check your llm-d cluster by running ${k} get secrets."

tokens=()
! [[ -z "${LLMDBENCH_HF_TOKEN}" ]] && tokens+=("${LLMDBENCH_HF_TOKEN} (LLMDBENCH_HF_TOKEN)")
! [[ -z "${HF_TOKEN}" ]] && tokens+=("${HF_TOKEN} (HF_TOKEN)")
cluster_token=$(
  $k -n "${LLMDBENCH_VLLM_COMMON_NAMESPACE}" get secrets "${HF_TOKEN_NAME}" -o jsonpath='{.data.*}' | base64 -d || true
)
! [[ -z "${cluster_token}" ]] && tokens+=("${cluster_token} (token from llm-d cluster)")
prompt="Please select HF TOKEN"
prompt2="Please enter HF TOKEN"
reply=$(choose_var -o "${prompt}" "${tokens[@]}") ||
  reply=$(read_var "${prompt2}") ||
  error_exit 1 "LLMDBENCH_HF_TOKEN cannot be empty."
export_var LLMDBENCH_HF_TOKEN ${reply}


# DIRECTORIES
# ===========
harness_dir="${LLMDBENCH_HARNESS_DIR:-/tmp}"
prompt="Please confirm tmp directory for harness git clone"
export_var LLMDBENCH_HARNESS_DIR $(read_var "${prompt}" "${harness_dir}") ||
  error_exit 1 "LLMDBENCH_HARNESS_DIR cannot be empty."

infra_dir="${LLMDBENCH_INFRA_DIR:-/tmp}"
prompt="Please confirm tmp directory for llm-d-infra git clone"
export_var LLMDBENCH_INFRA_DIR $(read_var "${prompt}" "${infra_dir}") ||
  error_exit 1 "LLLMDBENCH_INFRA_DIR cannot be empty."

base_dir=$(cd $(dirname $(readlink -f ${BASH_SOURCE[0]})) && pwd)  # Use script dir
result_dir="${LLMDBENCH_CONTROL_WORK_DIR:-${base_dir}/${LLMDBENCH_VLLM_COMMON_NAMESPACE}}"
prompt="Please confirm results directory for benchmark results"
export_var LLMDBENCH_CONTROL_WORK_DIR $(read_var "${prompt}" "${result_dir}") ||
  error_exit 1 "LLMDBENCH_CONTROL_WORK_DIR cannot be empty."


# STORAGE
# =======

# HARNESS PVC
NAME="NAME:.metadata.name"
VOLUME="VOLUME:.spec.volumeName"
CAPACITY="CAPACITY:.status.capacity.storage"
CLASS="STORAGECLASS:.spec.storageClassName"
MODE="MODE:.status.accessModes"
readarray -t pvcs < <(
  $k -n "${LLMDBENCH_VLLM_HARNESS_NAMESPACE}" get pvc \
    -o custom-columns="$NAME,$VOLUME,$CAPACITY,$CLASS,$MODE" ||
    true
)
header="   ${pvcs}"

# Persistent Volume Claim where benchmark results will be stored 
workload_pvcs=()
workload_pattern="^${LLMDBENCH_HARNESS_PVC_NAME:-###}($|\t| )"
model_pattern="^${LLMDBENCH_VLLM_COMMON_PVC_NAME:-###}($|\t| )"
for pvc in "${pvcs[@]:1}"; do
  if [[ "${pvc}" =~ ${workload_pattern} ]]; then
    workload_pvcs+=("${pvc} (current value)")
  elif [[ "${pvc}" =~ ${model_pattern} ]]; then
    :   # skip
  else
    workload_pvcs+=("${pvc}")
  fi
done

prompt="Please select PVC for benchmark workload results (choose 'other' to create a new PVC).
${header}"
prompt2="A new PVC will be created. Please enter a name for the new PVC"

if reply=$(choose_var -o "${prompt}" "${workload_pvcs[@]}"); then
  echo $reply
else
  reply=$(read_var "${prompt2}" "workload-pvc") ||
    error_exit 1 "LLMDBENCH_HARNESS_PVC_NAME cannot be empty."
  NAME="NAME:{.metadata.name}"
  PROVISIONER="PROVISIONER:{.provisioner}"
  DEFAULT=":{.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class}"

  readarray -t classes < <(
    ! [[ -z "${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS:-}" ]] && echo "${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS} (current value)"
    $k -n "${LLMDBENCH_VLLM_HARNESS_NAMESPACE}" get storageclasses \
      -o custom-columns="$NAME,$PROVISIONER,$DEFAULT" |
      sed -n -f <(cat <<SED
        # keep header
        1         {p;}
        # mark default, print as first entry
        /true$/   {s//(default)/p;}
        # remove none for non default, append to save buffer
        /<none>/  {s///;H;}
        # get saved entries, remove empty first newline, print
        $         {x;s/.//p;}
SED
      ) || true
  )

  header="   ${classes}"
  prompt="The storage class is used to create a PVC for storing benchmark workload results.
Please select a storage class for results PVC $reply
${header}"
  export_var LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS $(choose_var "${prompt}" "${classes[@]:1}") ||
    error_exit 1 "No available storage classes for LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS"
fi

export_var LLMDBENCH_HARNESS_PVC_NAME ${reply}


# HARNESS
# =======
harnesses=()
for harness in "inference-perf" "fmperf" "vllm-benchmark" "guidellm"; do
  if [[ "${harness}" == "${LLMDBENCH_HARNESS_NAME}" ]]; then
    harnesses+=("${harness} (current value)")
  else
    harnesses+=("${harness}")
  fi
done
prompt="The are several supported harness implementations. Each has its own workloads configuration syntax. 
Please choose harness"
export_var LLMDBENCH_HARNESS_NAME $(choose_var "${prompt}" "${harnesses[@]}")


# ENDPOINT and MODEL
# ==================
servicename=$(
  $k -n "${LLMDBENCH_VLLM_HARNESS_NAMESPACE}" get service \
    -l gateway.networking.k8s.io/gateway-name \
    --no-headers -o=custom-columns="NAME:{.metadata.name}" 2>/dev/null
  ) || echo "No inference service found in namespace ${LLMDBENCH_VLLM_HARNESS_NAMESPACE}" >&3
readarray -t methods < <(
  ! [[ -z "${LLMDBENCH_DEPLOY_METHODS:-}" ]] && echo "${LLMDBENCH_DEPLOY_METHODS} (current_value)"
  ! [[ -z "${servicename}" ]] && echo "${servicename} (gateway service)"
  $k -n "${LLMDBENCH_VLLM_HARNESS_NAMESPACE}" get pods \
    -l llm-d.ai/inferenceServing \
    --no-headers -o=custom-columns="NAME:{.metadata.name},:. \" (vLLM pod)\"" 2>/dev/null
  ) || echo "No vLLM pod found in namespace ${LLMDBENCH_VLLM_HARNESS_NAMESPACE}" >&3
prompt="Looking for valid endpoints.
Pleae select the inference endpoint to be benchmarked"
prompt2="Please enter an inference endpoint to be benchmarked"
reply=$(choose_var -o "${prompt}" "${methods[@]}") ||
  reply=$(read_var "${prompt2}") ||
  error_exit 1 "Inference endpoint (LLMDBENCH_DEPLOY_METHODS) cannot be empty."
export_var LLMDBENCH_DEPLOY_METHODS ${reply}

if ! [[ -z "${servicename:-}" ]]; then
  pid=""
  endpoint=$(
    $k -n ${LLMDBENCH_VLLM_COMMON_NAMESPACE} get route \
      -l gateway.networking.k8s.io/gateway-name \
      -o custom-columns='SERVICE:{.spec.to.name},HOST:{.spec.host},PORT:{.spec.port.targetPort}' 2>/dev/null |
      awk -v service="$servicename" '$1==service  {gsub(":default$", ":80", $2); print "http://" $2; exit}' ||
      true
  )
  if [[ -z "${endpoint:-}" ]]; then
    echo "No external route to inference service ${servicename}." >&3
    echo "Trying to detect model with port forwarding." >&3
    localport=8100
    remoteport=$($k -n ${LLMDBENCH_VLLM_COMMON_NAMESPACE} get service ${servicename} -o=jsonpath='{.spec.ports[].port}' |
      sed 's/default/80/')
    $k -n ${LLMDBENCH_VLLM_COMMON_NAMESPACE} port-forward "service/${servicename}" "${localport}":"${remoteport}" & pid=$!
    sleep 2
    endpoint="http://localhost:${localport}"
  fi
  modelname=$(curl -s ${endpoint}/v1/models | jq -r '.data[].id')
  ! [[ -z "${pid}" ]] && kill -9 ${pid}
else
  echo "Could not detect model name." >&3
  modelname=""
fi
prompt="Please specify model name"
export_var LLMDBENCH_DEPLOY_MODEL_LIST $(read_var "${prompt}" "${modelname:-${LLMDBENCH_DEPLOY_MODEL_LIST}}") ||
  error_exit 1 "Model name cannot be empty. Please make sure your are using the model of your llm-d cluster."
if ! [[ -z "${modelname}" ]] &&
  ! [[ -z "${LLMDBENCH_DEPLOY_MODEL_LIST}" ]] &&
  [[ "${modelname}" != "${LLMDBENCH_DEPLOY_MODEL_LIST}" ]]; then
  echo "WARNING:
  Current LLMDBENCH_DEPLOY_MODEL_LIST (${LLMDBENCH_DEPLOY_MODEL_LIST}) does not match llm-d stack model (${modelname})."
fi

# TIMEOUT
# =======
# This is a timeout (seconds) for running a full test
# If time expires the benchmark will still run but results will not be collected to local computer.
prompt="If the benchmark takes too long to complete then the benchmark client timesout.
Only the clinet aborts. The benchmark would complete its run on the cluster and the results are stored on the PVC.
The results can still be fetched later for analysis.
Please specify timeout to wait for benchmark completion."
export_var LLMDBENCH_HARNESS_WAIT_TIMEOUT $(read_var "${prompt}" "${LLMDBENCH_HARNESS_WAIT_TIMEOUT:-3600}" || true)
((1 + 1/${LLMDBENCH_HARNESS_WAIT_TIMEOUT})) 2>/dev/null || error_exit 1 "LLMDBENCH_HARNESS_WAIT_TIMEOUT must be a number (seconds)."

# OUTPUT
# ======
cat <<BASH | envsubst
# ==================================================
# ENV variables for llm-d-benchmark runs.sh
#
# Source before calling run.sh or use with -c option
# ==================================================

# NAMESPACE
# ---------
# [-p] namespace where llm-d stack is deployed
export LLMDBENCH_VLLM_COMMON_NAMESPACE="${LLMDBENCH_VLLM_COMMON_NAMESPACE}"
# namespace where harness will be run (same as llm-d stack)
export LLMDBENCH_VLLM_HARNESS_NAMESPACE="${LLMDBENCH_VLLM_HARNESS_NAMESPACE}"

# HF_TOKEN
# --------
# secret name when HF_TOKEN is stored in llm-d namespace
export HF_TOKEN_NAME="${HF_TOKEN_NAME}"
# HuggingFace token (default to HF_TOKEN or token secret in llm-d stack)
export LLMDBENCH_HF_TOKEN="${LLMDBENCH_HF_TOKEN}"

# DIRECTORIES
# -----------
# directory for git clone of harness
export LLMDBENCH_HARNESS_DIR="${LLMDBENCH_HARNESS_DIR}"
# directory for git clone of llm-d-infra
export LLMDBENCH_INFRA_DIR="${LLMDBENCH_INFRA_DIR}"
# directory for saving benchmark results
export LLMDBENCH_CONTROL_WORK_DIR="${LLMDBENCH_CONTROL_WORK_DIR}"

# STORAGE
# -------
# [-k] PVC for benchmark results
export LLMDBENCH_HARNESS_PVC_NAME="${LLMDBENCH_HARNESS_PVC_NAME}"
# Storage class for created PVCs
export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS="${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS}"

# BENCHMARK PARAMETERS
# --------------------
# [-l] harness to use for benchmarking
export LLMDBENCH_HARNESS_NAME="${LLMDBENCH_HARNESS_NAME}"
# [-t] endpoint name (service / vLLM)
export LLMDBENCH_DEPLOY_METHODS="${LLMDBENCH_DEPLOY_METHODS}"
# [-m] model being benchmarked
export LLMDBENCH_DEPLOY_MODEL_LIST="${LLMDBENCH_DEPLOY_MODEL_LIST}"
# [-s] how long to wait for results
export LLMDBENCH_HARNESS_WAIT_TIMEOUT="${LLMDBENCH_HARNESS_WAIT_TIMEOUT}"
BASH

