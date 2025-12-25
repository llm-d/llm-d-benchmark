#!/usr/bin/env bash

# Copyright 2025 The llm-d Authors.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

if uname -s | grep -qi darwin; then
  alias sed=gsed  
fi

# Constants
HARNESS_EXECUTABLE=llm-d-benchmark.sh
CURL_TIMEOUT=10

HARNESS_POD_LABEL="llmdbench-harness-launcher"
HARNESS_EXECUTABLE="llm-d-benchmark.sh"
HARNESS_CPU_NR=16
HARNESS_CPU_MEM=32Gi
RESULTS_DIR_PREFIX=/requests
KUBECTL_TIMEOUT=180
DATASET_DIR=/workspace


function show_usage {
  cat <<USAGE
Usage: ${_script_name} -c <config-file> [options]

  Runs llm-d-benchmark harness against an existing LLM deployment stack.

  Options:
    -c/--config path to configuration file
    -v/--verbose print the command being executed, and result
    -d/--debug execute harness in "debug-mode"
    -n/--dry-run do not execute commands, just print what would be executed
    -h/--help show this help
USAGE
}

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "This script should be executed not sourced" >&2
  show_usage
  return 1
fi

# Log announcement function
function announce {
    local message="${1}"
    local logfile=${2:-none}

    case ${logfile} in
        none|""|"1")
            echo
            echo "===> $(date) - ${0}:${LINENO}"
            echo -e "$message"
            echo -ne "\033[01;33m";   # br yellow
            echo "------------------------------------------------------------"
            echo -ne "\033[0m"
            ;;
        silent|"0")
            ;;
        *)
            echo -e "==> $(date) - ${0} - $message" >> ${logfile}
            ;;  
    esac
}

# Sanitize pod name to conform to Kubernetes naming conventions
function sanitize_pod_name {
  tr [:upper:] [:lower:] <<<"$1" | sed -e 's/[^0-9a-z-][^0-9a-z-]*/-/g' | sed -e 's/^-*//' | sed -e 's/-*$//'
}

# Sanitize directory name to conform to filesystem naming conventions
function sanitize_dir_name {
  sed -e 's/[^0-9A-Za-z_-][^0-9A-Za-z_-]*/_/g' <<<"$1"
}

# Generate results directory name
function results_dir_name {
  local stack_name="$1"
  local harness_name="$2"
  local experiment_id="$3"
  local workload_name="${4:+_$4}"

  sanitize_dir_name "${RESULTS_DIR_PREFIX}/${harness_name}_${experiment_id}${workload_name}_${stack_name}"
} 

# Retrieve list of available harnesses
function get_harness_list {
  ls ${LLMDBENCH_MAIN_DIR}/workload/harnesses | $LLMDBENCH_CONTROL_SCMD -e 's^inference-perf^inference_perf^' -e 's^vllm-benchmark^vllm_benchmark^' | cut -d '-' -f 1 | $LLMDBENCH_CONTROL_SCMD -n -e 's^inference_perf^inference-perf^' -e 's^vllm_benchmark^vllm-benchmark^' -e 'H;${x;s/\n/,/g;s/^,//;p;}'
}

function start_harness_pod {
  local pod_name=$1
  if [ "${harness_dataset_url:=none}" == "none" ]; then # make sure the variable is defined
    local is_dataset_url="# "   # used to comment out the dataset_url env var
  else
    local is_dataset_url=""
  fi  

  ${control_kubectl} --namespace ${harness_namespace} delete pod ${pod_name} --ignore-not-found

  cat <<EOF | yq '.spec.containers[0].env = load("'${_config_file}'").env + .spec.containers[0].env' | ${control_kubectl} apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: ${harness_namespace}
  labels:
    app: ${HARNESS_POD_LABEL}
spec:
  containers:
  - name: harness
    image: ${harness_image}
    imagePullPolicy: Always
    securityContext:
      runAsUser: 0
    command: ["sh", "-c"]
    args:
    - "sleep 1000000"
    resources:
      limits:
        cpu: "${HARNESS_CPU_NR}"
        memory: ${HARNESS_CPU_MEM}
      requests:
        cpu: "${HARNESS_CPU_NR}"
        memory: ${HARNESS_CPU_MEM}
    env:
    - name: LLMDBENCH_RUN_WORKSPACE_DIR
      value: "/workspace"
    - name: LLMDBENCH_MAGIC_ENVAR
      value: "harness_pod"
    - name: LLMDBENCH_HARNESS_NAME
      value: "${harness_name}"
    - name: LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX
      value: "${RESULTS_DIR_PREFIX}"
    - name: LLMDBENCH_RUN_DATASET_DIR
      value: "${DATASET_DIR}"
    ${is_dataset_url}- name: LLMDBENCH_RUN_DATASET_URL
    ${is_dataset_url}  value: "${harness_dataset_url}"
    - name: LLMDBENCH_HARNESS_STACK_NAME
      value: "${endpoint_stack_name}"  
    volumeMounts:
    - name: results
      mountPath: ${RESULTS_DIR_PREFIX}
    - name: "${harness_name}-profiles"
      mountPath: /workspace/profiles/${harness_name}  
  volumes:
  - name: results
    persistentVolumeClaim:
      claimName: $harness_results_pvc
  - name: ${harness_name}-profiles    
    configMap:
      name: ${harness_name}-profiles
  restartPolicy: Never    
EOF
  ${control_kubectl} wait --for=condition=Ready=True pod ${pod_name} -n ${harness_namespace} --timeout="${KUBECTL_TIMEOUT}s"
  if [[ $? != 0 ]]; then
    announce "❌ Timeout waiting for pod ${pod_name} to get ready"
    exit 1
  fi
  announce "ℹ️ Harness pod ${pod_name} started"
  ${control_kubectl} describe pod ${pod_name} -n ${harness_namespace}
}

set -euo pipefail
cd "$(dirname "$(realpath -- $0)")" > /dev/null 2>&1
_script_name="${0##*/}"
_control_dir=$(realpath $(pwd)/)
_root_dir=$(realpath "${_control_dir}/../")
_uid=$(date +%s)

#Parse command line arguments
# ========================================================
while [[ $# -gt 0 ]]; do
    key="$1"

    case $key in
        -c=*|--config=*)
        _config_file=$(echo $key | cut -d '=' -f 2)
        ;;
        -c|--config)
        _config_file="$2"
        shift
        ;;
        -n|--dry-run)
        export $kubectl=1
        ;;
        -d|--debug)
        export LLMDBENCH_HARNESS_DEBUG=1
        ;;
        -v|--verbose)
        export LLMDBENCH_VERBOSE=1
        ;;
        -h|--help)
        show_usage
        exit 0
        ;;
        *)
        announce "❌ ERROR: unknown option \"$key\""
        show_usage
        exit 1
        ;;
        esac
        shift
done

# Read configuration file
# ========================================================
announce "📄 Reading configuration file $_config_file"
if ! [[ -f $_config_file  ]]; then
  announce "❌ ERROR: could not find config file \"$_config_file\""
  exit 1
fi
eval $( yq -o shell '. | del(.workload)| del (.env)' "$_config_file")

if [[ "$harness_parallelism" != "1" ]]; then
    announce "❌ ERROR: harness_parallelism is set to '$harness_parallelism'. Only parallelism=1 is supported."
    exit 1
fi  
#@TODO harness_parallelism=1 only is supported for now!!!

_harness_pod_name=$(sanitize_pod_name "${HARNESS_POD_LABEL}")

announce "ℹ️ Using endpoint_stack_name=$endpoint_stack_name on endpoint_namespace=$endpoint_namespace running model=${endpoint_model} at endpoint_base_url=$endpoint_base_url"
announce "ℹ️ Using harness_name=$harness_name, with _harness_pod_name=$_harness_pod_name on harness_namespace=$harness_namespace"

# Ensure harness namespace is prepared
# ========================================================
announce "🔧 Ensuring harness namespace is prepared"
_control_dir=$(realpath $(pwd)/)

# Verify HF token secret exists
# ========================================================
announce "🔧 Verifying HF token secret ${endpoint_hf_token_secret} in namespace ${endpoint_namespace}"
if $control_kubectl --namespace "$endpoint_namespace" get secret "$endpoint_hf_token_secret" 2>&1 > /dev/null; then 
  announce "ℹ️ Using HF token secret $endpoint_hf_token_secret"
else    
  announce "❌ ERROR: could not fetch HF token secret $endpoint_hf_token_secret"
  exit 1
fi

# Verify model is deployed and endpoint is reachable
# ========================================================
_verify_model_pod_name=$(sanitize_pod_name "verify-model-${_uid}")
announce "🔍 Verifying model ${endpoint_model} on endpoint ${endpoint_base_url}/v1/completions using pod $_verify_model_pod_name"

set +e
$control_kubectl -n $endpoint_namespace run ${_verify_model_pod_name} \
    --request-timeout=${KUBECTL_TIMEOUT}s --pod-running-timeout=${KUBECTL_TIMEOUT}s \
    -q --rm -i --image=alpine/curl --restart=Never --command -- \
    curl -sS -m $CURL_TIMEOUT -i --fail-with-body "${endpoint_base_url}/v1/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "'${endpoint_model}'",
        "prompt": "Hello"
    }'
if [[ $? != 0 ]]; then
  announce "❌ Error while verifying model"
  exit 1
fi
set -e

# Prepare ConfigMap with workload profiles
# ========================================================
announce "🔧 Preparing ConfigMap with workload profiles"
$control_kubectl --namespace "${harness_namespace}" delete configmap ${harness_name}-profiles --ignore-not-found

cmd=($control_kubectl create cm ${harness_name}-profiles)
cmd+=(--namespace "${harness_namespace}")
for key in $(yq '.workload | keys | .[]' $_config_file); do
  cmd+=( --from-file=${key}.yaml='<(yq ".workload.'$key' | explode(.)" '$_config_file')')
done
eval ${cmd[@]}
announce "ℹ️ ConfigMap '${harness_name}-profiles' created"


# Check results PVC
# ========================================================
announce "ℹ️ Checking results PVC"
if ! $control_kubectl --namespace=${harness_namespace} describe pvc ${harness_results_pvc}; then
  announce "❌ Error checking PVC ${harness_results_pvc}"
fi

# Create harness pod
# ========================================================  
_pod_name="${_harness_pod_name}"    # place holder for parallelism support
announce "ℹ️ Creating harness pod ${_pod_name}"

set +e
start_harness_pod ${_pod_name}
set -e

# Execute workloads
# ========================================================
set +e
if [ "${harness_wait_timeout}" -eq 0 ]; then
  _timeout=""
else
  _timeout="timeout ${harness_wait_timeout}s"
fi
yq '.workload | keys | .[]' "${_config_file}" |
  while IFS= read -r workload; do
    announce "ℹ️ Running benchmark with workload ${workload}"
    ${_timeout} $control_kubectl exec -i ${_pod_name} -- bash <<RUN_WORKLOAD
# redirect to root fds so that kubectl logs can capture output
exec 1> >(tee /proc/1/fd/1 >&1)
exec 2> >(tee /proc/1/fd/2 >&2)

export LLMDBENCH_RUN_EXPERIMENT_ID="${_uid}_${workload}"

${HARNESS_EXECUTABLE} --harness="${harness_name}" --workload="${workload}"
RUN_WORKLOAD
    res=$?
    if [ $res -eq 0 ]; then
      announce "ℹ️ Benchmark workload ${workload} complete."
    elif [ $res -eq 124 ]; then
      announce "⚠️ Warning: workload ${workload} timed out after ${harness_wait_timeout}s."
    else 
      announce "❌ ERROR: error happened while running workload ${workload}."
    fi  
  done
set -e

# Finalization
# ========================================================
announce "✅ 
   Experiment ID is ${_uid}.
   All workloads completed. 
   Results should be available in PVC ${harness_results_pvc}.
   Please use analyze.sh to fetch and analyze results.
"