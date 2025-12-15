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

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "This script should be executed not sourced" >&2
  return 1
fi

cd "$(dirname "$(realpath -- "$0")")" > /dev/null 2>&1
export LLMDBENCH_EXISTING_STACK_DIR=$(realpath $(pwd)/)
pushd ../setup > /dev/null 2>&1
export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/)
popd > /dev/null 2>&1

set -euo pipefail

export LLMDBENCH_ENV_VAR_LIST=$(env | grep ^LLMDBENCH | cut -d '=' -f 1)
export LLMDBENCH_CONTROL_CALLER=$(echo $0 | rev | cut -d '/' -f 1 | rev)
export LLMDBENCH_STEPS_DIR="$LLMDBENCH_CONTROL_DIR/steps"

export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../)

source ${LLMDBENCH_CONTROL_DIR}/env.sh
function sanitize_dir_name {
  sed -e 's/[^0-9A-Za-z_-][^0-9A-Za-z_-]*/_/g' <<<"$1"
}

export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=
export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=
export LLMDBENCH_HARNESS_SKIP_RUN=${LLMDBENCH_HARNESS_SKIP_RUN:-0}
export LLMDBENCH_HARNESS_DEBUG=${LLMDBENCH_HARNESS_DEBUG:-0}
export LLMDBENCH_CURRENT_STEP=99

function show_usage {
  cat <<USAGE
Usage: ${LLMDBENCH_CONTROL_CALLER} [options] 
  -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO)]
  -m/--models [list the models to be run against (default=$LLMDBENCH_DEPLOY_MODEL_LIST), use "auto" to auto-detect the model served by the stack]
  -p/--namespace [comma separated pair of values indicating where a stack was stood up and where to run (default=$LLMDBENCH_VLLM_COMMON_NAMESPACE,$LLMDBENCH_HARNESS_NAMESPACE)]
  -t/--methods [list of standup methods (default=$LLMDBENCH_DEPLOY_METHODS, possible values "standalone", "modelservice" or any other string - pod name or service name - matching a resource on cluster)]
  -U/--endpoint_url [url of the stack endpoint to be benchmarked (default=$LLMDBENCH_HARNESS_STACK_ENDPOINT_URL); if provided , overrides method detection]
  -l/--harness [harness used to generate load (default=$LLMDBENCH_HARNESS_NAME, possible values $(get_harness_list)]
  -w/--workload [workload to be used by the harness (default=$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE, possible values (check \"${LLMDBENCH_HARNESS_PROFILES_DIR}\" dir)]
  -k/--pvc [name of the PVC used to store the results (default=$LLMDBENCH_HARNESS_PVC_NAME)]
  -e/--experiments [path of yaml file containing a list of factors and levels for an experiment, useful for parameter sweeping (default=$LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS)]
  -o/--overrides [comma-separated list of workload profile parameters to be overridden (default=$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES)]
  -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE)]
  -x/--dataset [url for dataset to be replayed (default=$LLMDBENCH_RUN_DATASET_URL)]
  -j/--parallelism [number of harness pods to be created (default=$LLMDBENCH_HARNESS_LOAD_PARALLELISM)]
  -s/--wait [time to wait until the benchmark run is complete (default=$LLMDBENCH_HARNESS_WAIT_TIMEOUT, value "0" means "do not wait"]
  -d/--debug [execute harness in "debug-mode" (default=$LLMDBENCH_HARNESS_DEBUG)]
  -h/--help (show this help)"
USAGE
}

while [[ $# -gt 0 ]]; do
  key="$1"

  case $key in
    -c=*|--scenario=*)
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=$(echo $key | cut -d '=' -f 2)
    ;;
    -c|--scenario)
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO="$2"
    shift
    ;;
    -m=*|--models=*)
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_MODEL_LIST=$(echo $key | cut -d '=' -f 2)
    export LLMDBENCH_ENV_VAR_LIST=$LLMDBENCH_ENV_VAR_LIST" LLMDBENCH_DEPLOY_MODEL_LIST"
    ;;
    -m|--models)
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_MODEL_LIST="$2"
    export LLMDBENCH_ENV_VAR_LIST=$LLMDBENCH_ENV_VAR_LIST" LLMDBENCH_DEPLOY_MODEL_LIST"
    shift
    ;;
    -p=*|--namespace=*)
    export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE=$(echo $key | cut -d '=' -f 2 | cut -d ',' -f 1)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE=$(echo $key | cut -d '=' -f 2 | cut -d ',' -f 2)
    if [[ -z $LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE ]]; then
      export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE=$LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE
    fi
    ;;
    -p|--namespace)
    export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE="$(echo $2 | cut -d ',' -f 1)"
    export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE="$(echo $2 | cut -d ',' -f 2)"
    if [[ -z $LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE ]]; then
      export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE=$LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE
    fi
    shift
    ;;
    -j=*|--parallelism=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_LOAD_PARALLELISM=$(echo $key | cut -d '=' -f 2)
    ;;
    -j|--parallelism)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_LOAD_PARALLELISM="$2"
    shift
    ;;
    -s=*|--wait=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_WAIT_TIMEOUT=$(echo $key | cut -d '=' -f 2)
    ;;
    -s|--wait)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_WAIT_TIMEOUT="$2"
    shift
    ;;
    -l=*|--harness=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_NAME=$(echo $key | cut -d '=' -f 2)
    ;;
    -l|--harness)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_NAME="$2"
    shift
    ;;
    -k=*|--pvc=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_PVC_NAME=$(echo $key | cut -d '=' -f 2)
    ;;
    -k|--pvc)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_PVC_NAME="$2"
    shift
    ;;
    -w=*|--workload=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE=$(echo $key | cut -d '=' -f 2)
    ;;
    -w|--workload)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE="$2"
    shift
    ;;
    -e=*|--experiment=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_TREATMENTS=$(echo $key | cut -d '=' -f 2)
    ;;
    -e|--experiment)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_TREATMENTS="$2"
    shift
    ;;
    -o=*|--overrides=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE_OVERRIDES=$(echo $key | cut -d '=' -f 2)
    ;;
    -o|--overrides)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE_OVERRIDES="$2"
    shift
    ;;
    -t=*|--methods=*)
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS=$(echo $key | cut -d '=' -f 2)
    ;;
    -t|--methods)
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS="$2"
    shift
    ;;
    -U=*|--endpoint_url=*)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_STACK_ENDPOINT_URL=$(echo $key | cut -d '=' -f 2)
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS="${LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS:=URL}"
    ;;
    -U|--undpoint_url)
    export LLMDBENCH_CLIOVERRIDE_HARNESS_STACK_ENDPOINT_URL="$2"
    export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS="${LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS:=URL}"
    shift
    ;;
    -x=*|--dataset=*)
    export LLMDBENCH_CLIOVERRIDE_RUN_DATASET_URL=$(echo $key | cut -d '=' -f 2)
    ;;
    -x|--dataset)
    export LLMDBENCH_CLIOVERRIDE_RUN_DATASET_URL="$2"
    shift
    ;;
    # -z|--skip)
    # export LLMDBENCH_CLIOVERRIDE_HARNESS_SKIP_RUN=1
    # ;;
    -d|--debug)
    export LLMDBENCH_HARNESS_DEBUG=1
    ;;
    -v|--verbose)
    export LLMDBENCH_CLIOVERRIDE_CONTROL_VERBOSE=1
    export LLMDBENCH_CONTROL_VERBOSE=1
    export LLMDBENCH_ENV_VAR_LIST=$LLMDBENCH_ENV_VAR_LIST" LLMDBENCH_CONTROL_VERBOSE"
    ;;
    -h|--help)
    show_usage
    if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
    then
        exit 0
    else
        return 0
    fi
    ;;
    *)
    echo "ERROR: unknown option \"$key\""
    show_usage
    exit 1
    ;;
  esac
  shift
done

export LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED=1

source ${LLMDBENCH_CONTROL_DIR}/env.sh

export LLMDBENCH_BASE64_CONTEXT_CONTENTS=$LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx

set +euo pipefail
export LLMDBENCH_CURRENT_STEP=05
if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 0 && $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 0 ]]; then
  export LLMDBENCH_VLLM_MODELSERVICE_URI_PROTOCOL="NA"

  if [[ -z $LLMDBENCH_CONTROL_CLUSTER_NAMESPACE ]]; then
    announce "❌ Unable automatically detect namespace. Environment variable \"LLMDBENCH_CONTROL_CLUSTER_NAMESPACE\". Specifiy namespace via CLI option \"-p\--namespace\" or environment variable \"LLMDBENCH_HARNESS_NAMESPACE\""
    exit 1
  fi
fi

# @TODO extract the absolute must tasks for run only. Separate privileged operations 
python3 ${LLMDBENCH_STEPS_DIR}/05_ensure_harness_namespace_prepared.py 2> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stderr.log 1> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stdout.log
if [[ $? -ne 0 ]]; then
  announce "❌ Error while attempting to setup the harness namespace"
  cat ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stderr.log
  echo "---------------------------"
  cat ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stdout.log
  exit 1
fi
set -euo pipefail

export LLMDBENCH_CURRENT_STEP=99
: ${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL:=}

for method in ${LLMDBENCH_DEPLOY_METHODS//,/ }; do
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do

    announce "ℹ️ Preparing to benchmark existing stack deployed via method \"$method\" for model \"$model\""

    if [[ $LLMDBENCH_HARNESS_DEBUG -eq 1 ]]; then
      export LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME=llmdbench-harness-launcher
    else
      export LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME=llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher
    fi

    export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=
    export LLMDBENCH_VLLM_FQDN=".${LLMDBENCH_VLLM_COMMON_NAMESPACE}${LLMDBENCH_VLLM_COMMON_FQDN}"

    if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
      export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="LLMDBENCH_RUN_EXPERIMENT|LLMDBENCH_BASE64_CONTEXT_CONTENTS|^LLMDBENCH_VLLM_COMMON|^LLMDBENCH_VLLM_STANDALONE|^LLMDBENCH_DEPLOY|^LLMDBENCH_HARNESS|^LLMDBENCH_RUN"
      export LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers -l stood-up-via=${LLMDBENCH_DEPLOY_METHODS} | awk '{print $1}' || true)
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=${LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME}${LLMDBENCH_VLLM_FQDN}
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=80
    fi

    if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
      export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="LLMDBENCH_RUN_EXPERIMENT|LLMDBENCH_BASE64_CONTEXT_CONTENTS|^LLMDBENCH_VLLM_COMMON|^LLMDBENCH_VLLM_MODELSERVICE|^LLMDBENCH_DEPLOY|^LLMDBENCH_VLLM_INFRA|^LLMDBENCH_VLLM_GAIE|^LLMDBENCH_LLMD_IMAGE|^LLMDBENCH_HARNESS|^LLMDBENCH_RUN"
      export LLMDBENCH_HARNESS_STACK_TYPE=llm-d
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_INFO=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get gateway --no-headers -l stood-up-via=${LLMDBENCH_DEPLOY_METHODS} -o json)
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(echo $LLMDBENCH_HARNESS_STACK_ENDPOINT_INFO | jq -r '.items[0].status.addresses[0] | select(.type=="Hostname") | .value')
        if [[ $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME == "null" || -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(echo $LLMDBENCH_HARNESS_STACK_ENDPOINT_INFO | jq -r '.items[0].metadata.name')
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=${LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME}${LLMDBENCH_VLLM_FQDN}
        fi
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=80
    fi
  
    if [[ -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_URL ]] &&  
       [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 || 
          $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then

      if [[ -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
        announce "❌ ERROR: could not find an endpoint name for a stack deployed via method \"$LLMDBENCH_DEPLOY_METHODS\" (i.e., with label \"stood-up-via=$LLMDBENCH_DEPLOY_METHODS\")"
        announce "📌 Tip: If the llm-d stack you're trying to benchmark was NOT deployed via \"standup.sh\", just use \"run.sh -t <string that matches the service/gateway name>\""
        exit 1
      fi
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_URL="http://${LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME}${LLMDBENCH_VLLM_FQDN}:${LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT}"
    else
      announce "⚠️ Deployment method - $LLMDBENCH_DEPLOY_METHODS - is neither \"standalone\" nor \"modelservice\". "
      export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="LLMDBENCH_RUN_EXPERIMENT|LLMDBENCH_BASE64_CONTEXT_CONTENTS|^LLMDBENCH_VLLM_COMMON_NAMESPACE|^LLMDBENCH_DEPLOY_CURRENT|^LLMDBENCH_HARNESS|^LLMDBENCH_RUN"
      export LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod
      # export LLMDBENCH_DEPLOY_CURRENT_MODEL="auto"  # @TODO check if needed
    fi

    if [[ -z ${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL} ]]; then   
      # note: url already set for standalone/modelservice
      announce "🔍 Trying to find a matching endpoint name on namespace ($LLMDBENCH_VLLM_COMMON_NAMESPACE)..."

      # check for service first
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers | awk '{print $1}' | grep -m 1 ${LLMDBENCH_DEPLOY_METHODS} || true)
      if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
        announce "ℹ️ ${LLMDBENCH_DEPLOY_METHODS} detected as service \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME\""
        for i in default http; do
          export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json | jq -r ".spec.ports[] | select(.name == \"$i\") | .port")
          if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT ]]; then
            break
          fi
        done
        if [[ -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT ]]; then
          announce "❌ ERROR: could not find a port for endpoint name \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME\""
          exit 1
        fi
      else
        # check for pod next
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod --no-headers | awk '{print $1}' | grep -m 1 ${LLMDBENCH_DEPLOY_METHODS} | head -n 1 || true)
        export LLMDBENCH_VLLM_FQDN=
        if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
          announce "ℹ️ ${LLMDBENCH_DEPLOY_METHODS} detected as pod \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME\""
          # try to get port from liveness or readiness probe
          for probe in livenessProbe readinessProbe; do
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=$(
              ${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json |
              jq -r ".spec.containers[0].${probe}.httpGet.port" ||
              true
            )
            if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT && $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT != "null" ]]; then
              break
            fi
          done
          if [[ -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT || $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT == "null" ]]; then
            # try to use metrics port (should work for default vLLM
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=$(
              ${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json |
              jq -r ".spec.containers[0].ports[] | select(.name == \"metrics\") | .containerPort" ||
              true
            )
          fi
          if [[ -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT || $LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT == "null" ]]; then
            announce "❌ ERROR: could not find a port for endpoint name \"$$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME\""
            exit 1
          fi
          export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json | jq -r ".status.podIP")
        else
          announce "❌ ERROR: could not find an endpoint name (service or pod) for a stack that matches \"$LLMDBENCH_DEPLOY_METHODS\""
          exit 1
        fi
      fi
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_URL="http://${LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME}${LLMDBENCH_VLLM_FQDN}:${LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT}"
    else
      export LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
    fi  # if no endpoint url provided
    announce "ℹ️ Using Stack Endpoint URL \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_URL\""


    # =============================================================
    # cleanup_pre_execution # @TODO -- verify if this is needed -- moved to run script
    
    
    if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 0 && $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 0 ]]; then
      if ! ${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get secret "${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}" 2>&1 > /dev/null; then
        export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get secrets --no-headers | grep -m 1 -E llm-d-hf.*token.* | awk '{print $1}')
        if [[ -z $LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME ]]; then
          announce "❌ ERROR: could not find a hugging face token"
          exit 1
        fi
      fi
      announce "ℹ️ Using hugging face token \"$LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME\""
    fi

    # Model
    # ========================================================
    announce "🔍 Trying to detect the model name served by the stack ($LLMDBENCH_HARNESS_STACK_ENDPOINT_URL)..."
    set +euo pipefail
    received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL} NA)
    if [[ -z $received_model_name ]]; then
      announce "⚠️ Unable to detect stack model!"
    fi
    set -euo pipefail

    if [[ "${model}" == "auto" ]]; then
      if [[ -z $received_model_name ]]; then
        announce "❌ ERROR: model detection failed while model set to \"auto\""
        exit 1
      else
        model=$received_model_name
        announce "ℹ️ Model set to \"auto\", using detected stack model \"$model\""
      fi
    else
      validate_model_name ${model}
    fi

    if [[ "${received_model_name}" == "${model}" ]]; then
      announce "ℹ️ Stack model detected is \"$received_model_name\", matches requested \"$model\""
    elif [[ -z "${received_model_name}" ]]; then
      announce "⚠️ Requested model \"$model\" could not be detected on stack"
    else
      announce "❌ Stack model detected is \"$received_model_name\" (instead of \"$model\")!"
      # exit 1    # @TODO decide if this is fatal
    fi

    export LLMDBENCH_DEPLOY_CURRENT_MODEL=$(model_attribute "${model}" model)
    export LLMDBENCH_DEPLOY_CURRENT_MODELID=$(model_attribute "${model}" modelid)
    export LLMDBENCH_DEPLOY_CURRENT_TOKENIZER=$(model_attribute "${model}" model)
    export LLMDBENCH_HARNESS_STACK_NAME=$(echo ${method} | $LLMDBENCH_CONTROL_SCMD 's^modelservice^llm-d^g')-$(model_attribute "${model}" parameters)-$(model_attribute "${model}" modeltype)


    # Prepare workload profiles
    # ========================================================
    rm -rf ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/*

    # if [[ $LLMDBENCH_HARNESS_DEBUG -eq 1 ]]; then ... # @TODO enable debug mode?

    generate_profile_parameter_treatments ${LLMDBENCH_HARNESS_NAME} ${LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS}

    workload_template_full_path=$(find ${LLMDBENCH_HARNESS_PROFILES_DIR}/${LLMDBENCH_HARNESS_NAME}/ | grep -m 1 ${LLMDBENCH_HARNESS_EXPERIMENT_PROFILE} | head -n 1 || true)
    if [[ -z $workload_template_full_path ]]; then
      announce "❌ Could not find workload template \"$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE\" inside directory \"${LLMDBENCH_HARNESS_PROFILES_DIR}/${LLMDBENCH_HARNESS_NAME}/\" (variable $LLMDBENCH_HARNESS_EXPERIMENT_PROFILE)"
      exit 1
    fi

    render_workload_templates ${LLMDBENCH_HARNESS_EXPERIMENT_PROFILE}

    # @TODO check if needed
    # export LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST=$LLMDBENCH_HARNESS_NAME
    # export LLMDBENCH_RUN_EXPERIMENT_HARNESS=$(find ${LLMDBENCH_MAIN_DIR}/workload/harnesses -name ${LLMDBENCH_HARNESS_NAME}* | rev | cut -d '/' -f1 | rev)
    # export LLMDBENCH_RUN_EXPERIMENT_ANALYZER=$(find ${LLMDBENCH_MAIN_DIR}/analysis/ -name ${LLMDBENCH_HARNESS_NAME}* | rev | cut -d '/' -f1 | rev)

    # @TODO verify if needed
    export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="^$(echo $LLMDBENCH_HARNESS_ENVVARS_TO_YAML | $LLMDBENCH_CONTROL_SCMD -e 's/,/|^/g' -e 's/$/|^/g')$LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD"

    export LLMDBENCH_RUN_EXPERIMENT_ID_PREFIX=""

    
    # Convert environment variables to YAML configuration as input for run 
    # ============================================================
    announce "ℹ️ Preparing run_configuration for harness \"$LLMDBENCH_HARNESS_NAME\" (profile: \"$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE\")"
    config_file_name="${workload_template_full_path##*/}"
    config_file_name="${config_file_name%.in}"
    config_file_name="${LLMDBENCH_HARNESS_NAME}_${config_file_name%.yaml}".yaml
    config_file_path="${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${config_file_name}"
    
    : >| "${config_file_path}"
    exec 3> >(tee -a "${config_file_path}")
    cat <<YAML >&3
endpoint:
  stack_name: &stack_name $(sanitize_dir_name "${LLMDBENCH_HARNESS_STACK_NAME}")    # user defined name for the stack (results prefix)
  model: &model $LLMDBENCH_DEPLOY_CURRENT_MODEL    # Exact HuggingFace model name. Must match stack deployed.
  namespace: &namespace $LLMDBENCH_CONTROL_CLUSTER_NAMESPACE    # Namespace where stack is deployed
  base_url: &url $LLMDBENCH_HARNESS_STACK_ENDPOINT_URL    # Base URL of inference endpoint
  hf_token_secret: $LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME    # The name of secret that contains the HF token of the stack
  model_pvc: $LLMDBENCH_VLLM_COMMON_PVC_NAME    # PVC where model files are cached


control:
  work_dir: $(realpath $LLMDBENCH_CONTROL_WORK_DIR)    # working directory to store temporary and autogenerated files. 
      # Do not edit content manually. If not set, a temp directory will be created. 
  kubectl: ${LLMDBENCH_CONTROL_KCMD%% *}    # kubectl command: kubectl or oc (context removed)                                   
  wait_timeout: $LLMDBENCH_HARNESS_WAIT_TIMEOUT    # Time (in seconds) to wait for workload launcher pod to complete before terminating.
      # Set to 0 to disable timeout. # Note: workload launcher pod will continue running in cluster if timeout occurs.
  

harness:
  name: &harness_name $LLMDBENCH_HARNESS_NAME    # inference-perf/llm-d-benchmark/guidellm/...
  results_pvc: $LLMDBENCH_HARNESS_PVC_NAME    # PVC where benchmark results are stored
  namespace: $LLMDBENCH_HARNESS_NAMESPACE    # Namespace where harness is deployed. Typically with stack.
  parallelism: $LLMDBENCH_HARNESS_LOAD_PARALLELISM    # Number of parallel workload launcher pods to create.  
  image: $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG})
  experiment_prefix: [ *stack_name, *harness_name ]
  dataset_url: &dataset_url ${LLMDBENCH_RUN_DATASET_URL:-none}    # URL to download dataset from

workload:    # yaml configuration for harness workload(s)
YAML

    for treatment_path in $(ls ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/${LLMDBENCH_HARNESS_NAME}/*.yaml); do
      workload_file=$(echo "${treatment_path}" | rev | cut -d '/' -f 1 | rev)
      treatment_file=$(cat "${treatment_path}" | grep -m 1 "#treatment" | tail -1 | $LLMDBENCH_CONTROL_SCMD 's/^#//' || true)
      if [[ -f "${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/${LLMDBENCH_HARNESS_NAME}/treatment_list/$treatment_file" ]]; then
        workload_name=$(sanitize_dir_name "${treatment_file%.txt}")
      else
        workload_name=$(sanitize_dir_name "${workload_file%.yaml}")
      fi
      announce "ℹ️ Adding workload profile ${workload_name} from file ${treatment_path} to harness config ${config_file_path}"
      echo "  # Workload from file ${treatment_path}" >&3
      yq -P '. as $root | {} | .workload.'${workload_name}' = $root' "${treatment_path}" | 
        $LLMDBENCH_CONTROL_SCMD '1d' >&3
    done  # treatment loop
    exec 3>&-
    announce "✅ Generated run configuration at ${config_file_path}"

  done  # model loop
done  # method loop
