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
    -o/--output destination for the results. (e.g. local/folder, gs://my-bucket, s3://my-bucket)
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

function upload_results {
  local pod_name=$1
  local storage_type=$2
  local destination=$3

  local local_results_dir=$(mktemp -d)
  if [[ "${storage_type}" == "local" ]]; then
    local_results_dir="${destination}/${_uid}"
    local provider="local"
  else
    local provider=$(echo "${destination}" | cut -d: -f1)
  fi
  mkdir -p ${local_results_dir}
  announce "📂 Copying results from pod ${pod_name} to directory '${destination}'"
  $control_kubectl cp "${pod_name}:${RESULTS_DIR_PREFIX}/." "${local_results_dir}" -n "${harness_namespace}"

  case ${provider} in
    gs)
      announce "☁️ Uploading results to GCS bucket ${destination}"
      gcloud storage cp --recursive "${local_results_dir}/" "${destination}/${_uid}/"
      ;;
    s3)
      announce "☁️ Uploading results to S3 bucket ${destination}"
      aws s3 cp --recursive "${local_results_dir}/" "${destination}/${_uid}/"
      ;;
    local)
      announce "ℹ️ Results saved to local folder."
      ;;
    *)
      announce "❌ ERROR: unknown or unsupported storage provider \"${provider}\"."
      exit 1
      ;;
  esac
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
  local storage_type=$2 # "pvc", "local" or "cloud"

  if [ "${harness_dataset_url:=none}" == "none" ]; then
    local is_dataset_url="# "
  else
    local is_dataset_url=""
  fi

  local volume_def="(.spec.volumes[] | select(.name == \"results\"))"
  if [[ "$storage_type" == "pvc" ]]; then
    volume_def="${volume_def}.persistentVolumeClaim.claimName = \"${harness_results_pvc}\"";
  elif [[ "$storage_type" == "local" ]] || [[ "$storage_type" == "cloud" ]]; then
    volume_def="${volume_def}.emptyDir = {}";
  else
    announce "❌ Error: Unsupport storage type '${storage_type}'."
    exit 1
  fi

  ${control_kubectl} --namespace ${harness_namespace} delete pod ${pod_name} --ignore-not-found

  cat <<EOF | yq "${volume_def}" | yq '.spec.containers[0].env = load("'${_config_file}'").env + .spec.containers[0].env' | ${control_kubectl} apply -f -
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
        -o=*|--output=*)
        _output_destination=$(echo $key | cut -d '=' -f 2)
        ;;
        -o|--output)
        _output_destination="$2"
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

# Verify output destination
# ========================================================
announce "🔎 Verifying output destination"
if [[ -z "${_output_destination:-}" ]]; then
  _storage_type="pvc"
  # PVC mode check
  announce "ℹ️ Verifying results PVC ${harness_results_pvc}"
  if ! $control_kubectl --namespace=${harness_namespace} describe pvc ${harness_results_pvc} &> /dev/null; then
    announce "❌ Error: results PVC '${harness_results_pvc}' not found in namespace '${harness_namespace}'. Please ensure it exists."
    exit 1
  fi
else
  if [[ "${_output_destination}" == *"://"* ]]; then
    _storage_type="cloud"
    _scheme=$(echo "${_output_destination}" | cut -d: -f1)
    case "${_scheme}" in
      gs)
        announce "ℹ️ Verifying GCS output destination..."
        if ! command -v gcloud &> /dev/null; then
          announce "❌ 'gcloud' command not found, but is required for 'gs://' output."
          exit 1
        fi
        ;;
      s3)
        announce "ℹ️ Verifying S3 output destination..."
        if ! command -v aws &> /dev/null; then
          announce "❌ 'aws' command not found, but is required for 's3://' output."
          exit 1
        fi
        ;;
      *)
        announce "❌ ERROR: Unsupported cloud provider scheme '${_scheme}' for destination '${_output_destination}'."
        exit 1
        ;;
    esac
  else
    _storage_type="local"
    announce "ℹ️ Verifying local output destination '${_output_destination}'"
    parent_dir=$(dirname "${_output_destination}")
    mkdir -p "${parent_dir}"
    if [[ ! -w "${parent_dir}" ]]; then
      announce "❌ ERROR: Output directory '${parent_dir}' is not writable."
      exit 1
    fi
  fi
fi

if [[ "$harness_parallelism" != "1" ]]; then
    announce "❌ ERROR: harness_parallelism is set to '$harness_parallelism'. Only parallelism=1 is supported."
    exit 1
fi  
#@TODO harness_parallelism=1 only is supported for now!!!
#@TODO: The 'upload_results' function currently handles only one pod. 
#       To support parallelism, it must collect results from all harness pods.

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


# Create harness pod
# ========================================================  
_pod_name="${_harness_pod_name}"    # place holder for parallelism support
announce "ℹ️ Creating harness pod ${_pod_name}"

set +e
start_harness_pod ${_pod_name} ${_storage_type}
set -e

# Execute workloads
# ========================================================
set +e
announce "ℹ️
  Running benchmark with Experiment ID ${_uid}.
  Results will be stored in PVC ${harness_results_pvc}.

  Note: 
    Benchmark will continue to run even on time-out or connection failure.
    Can follow progress by checking the logs (${control_kubectl} logs -f ${_pod_name} -n ${harness_namespace}).
"
if [ "${harness_wait_timeout}" -eq 0 ]; then
  _timeout=""
else
  _timeout="timeout ${harness_wait_timeout}s"
fi
yq '.workload | keys | .[]' "${_config_file}" |
  while IFS= read -r workload; do
    announce "ℹ️ Running benchmark with workload ${workload}."
    IFS= read -r -d '' run_workload <<RUN_WORKLOAD
      # redirect to root fds so that kubectl logs can capture output
      exec 1> >(tee /proc/1/fd/1 >&1)
      exec 2> >(tee /proc/1/fd/2 >&2)

      export LLMDBENCH_RUN_EXPERIMENT_ID="${_uid}_${workload}"

      ${HARNESS_EXECUTABLE} --harness="${harness_name}" --workload="${workload}"
RUN_WORKLOAD
    ${_timeout} $control_kubectl exec -i ${_pod_name} -n ${harness_namespace} -- bash -c "$run_workload"
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
case "${_storage_type}" in
  pvc)
    final_msg="PVC ${harness_results_pvc}.\nPlease use analyze.sh to fetch and analyze results."
    ;;
  local|cloud)
    upload_results "${_pod_name}" "${_storage_type}" "${_output_destination}"
    if [[ "${_storage_type}" == "local" ]]; then
        final_msg="Local Directory $(realpath "$_output_destination")."
    else
        final_msg="Storage Bucket ${_output_destination}/${_uid}."
    fi
    ;;
esac

announce "✅ 
  Experiment ID is ${_uid}.
  All workloads completed. 
  Results should be available in ${final_msg}
"
