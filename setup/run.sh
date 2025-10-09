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

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_ENV_VAR_LIST=$(env | grep ^LLMDBENCH | cut -d '=' -f 1)
export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/)
export LLMDBENCH_CONTROL_CALLER=$(echo $0 | rev | cut -d '/' -f 1 | rev)
export LLMDBENCH_STEPS_DIR="$LLMDBENCH_CONTROL_DIR/steps"

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../)

source ${LLMDBENCH_CONTROL_DIR}/env.sh

export LLMDBENCH_CONTROL_DRY_RUN=${LLMDBENCH_CONTROL_DRY_RUN:-0}
export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=
export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=
export LLMDBENCH_HARNESS_SKIP_RUN=${LLMDBENCH_HARNESS_SKIP_RUN:-0}
export LLMDBENCH_HARNESS_DEBUG=${LLMDBENCH_HARNESS_DEBUG:-0}
export LLMDBENCH_CURRENT_STEP=99

function show_usage {
    echo -e "Usage: ${LLMDBENCH_CONTROL_CALLER} -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
             -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO)] \n \
             -m/--models [list the models to be run against (default=$LLMDBENCH_DEPLOY_MODEL_LIST)] \n \
             -p/--namespace [comma separated pair of values indicating where a stack was stood up and where to run (default=$LLMDBENCH_VLLM_COMMON_NAMESPACE,$LLMDBENCH_HARNESS_NAMESPACE)] \n \
             -t/--methods [list of standup methods (default=$LLMDBENCH_DEPLOY_METHODS, possible values \"standalone\", \"modelservice\" or any other string - pod name or service name - matching a resource on cluster)] \n \
             -l/--harness [harness used to generate load (default=$LLMDBENCH_HARNESS_NAME, possible values $(get_harness_list)] \n \
             -w/--workload [workload to be used by the harness (default=$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE, possible values (check \"workload/profiles\" dir)] \n \
             -k/--pvc [name of the PVC used to store the results (default=$LLMDBENCH_HARNESS_PVC_NAME)] \n \
             -e/--experiments [path of yaml file containing a list of factors and levels for an experiment, useful for parameter sweeping (default=$LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS)] \n \
             -o/--overrides [comma-separated list of workload profile parameters to be overriden (default=$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES)] \n \
             -z/--skip [skip the execution of the experiment, and only collect data (default=$LLMDBENCH_HARNESS_SKIP_RUN)] \n \
             -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE)] \n \
             -x/--dataset [url for dataset to be replayed (default=$LLMDBENCH_RUN_DATASET_URL)]
             -s/--wait [time to wait until the benchmark run is complete (default=$LLMDBENCH_HARNESS_WAIT_TIMEOUT, value \"0\" means "do not wait\""] \n \
             -d/--debug [execute harness in \"debug-mode\" (default=$LLMDBENCH_HARNESS_DEBUG)] \n \
             -h/--help (show this help)"
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
        -n|--dry-run)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DRY_RUN=1
        export LLMDBENCH_ENV_VAR_LIST=$LLMDBENCH_ENV_VAR_LIST" LLMDBENCH_CONTROL_DRY_RUN"
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
        -x=*|--dataset=*)
        export LLMDBENCH_CLIOVERRIDE_RUN_DATASET_URL=$(echo $key | cut -d '=' -f 2)
        ;;
        -x|--dataset)
        export LLMDBENCH_CLIOVERRIDE_RUN_DATASET_URL="$2"
        shift
        ;;
        -z|--skip)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_SKIP_RUN=1
        ;;
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

  if [[ $LLMDBENCH_HARNESS_SERVICE_ACCOUNT == ${LLMDBENCH_HARNESS_NAME}-runner ]]; then
    LLMDBENCH_HARNESS_SERVICE_ACCOUNT=default
  fi
  announce "⚠️ Setting service account to \"$LLMDBENCH_HARNESS_SERVICE_ACCOUNT\"..."
fi

source ${LLMDBENCH_STEPS_DIR}/05_ensure_harness_namespace_prepared.sh 2> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stderr.log 1> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stdout.log
if [[ $? -ne 0 ]]; then
  announce "❌ Error while attempting to setup the harness namespace"
  cat ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stderr.log
  echo "---------------------------"
  cat ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/05_ensure_harness_namespace_prepare_stdout.log
  exit 1
fi
set -euo pipefail

export LLMDBENCH_CURRENT_STEP=99

for method in ${LLMDBENCH_DEPLOY_METHODS//,/ }; do

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    export LLMDBENCH_DEPLOY_CURRENT_MODEL=$(model_attribute $model model)
    export LLMDBENCH_DEPLOY_CURRENT_MODELID=$(model_attribute $model modelid)

    export LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME=llmdbench-${LLMDBENCH_HARNESS_NAME}-launcher

    validate_model_name ${LLMDBENCH_DEPLOY_CURRENT_MODEL}

    export LLMDBENCH_HARNESS_STACK_NAME=$(echo ${method} | $LLMDBENCH_CONTROL_SCMD 's^modelservice^llm-d^g')-$(model_attribute $model parameters)-$(model_attribute $model modeltype)

    export LLMDBENCH_DEPLOY_CURRENT_TOKENIZER=$(model_attribute $model model)

    if [[ $LLMDBENCH_HARNESS_SKIP_RUN -eq 1 ]]; then
      announce "⏭️ Command line option \"-z\--skip\" invoked. Will skip experiment execution (and move straight to analysis)"
    else
      cleanup_pre_execution

      export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=
      export LLMDBENCH_VLLM_FQDN=".${LLMDBENCH_VLLM_COMMON_NAMESPACE}${LLMDBENCH_VLLM_COMMON_FQDN}"

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
        export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="LLMDBENCH_BASE64_CONTEXT_CONTENTS|^LLMDBENCH_VLLM_COMMON|^LLMDBENCH_VLLM_STANDALONE|^LLMDBENCH_DEPLOY"
        export LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers -l stood-up-via=${LLMDBENCH_DEPLOY_METHODS} | awk '{print $1}' || true)
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=80
      fi

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
        export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="LLMDBENCH_BASE64_CONTEXT_CONTENTS|^LLMDBENCH_VLLM_COMMON|^LLMDBENCH_VLLM_MODELSERVICE|^LLMDBENCH_DEPLOY|^LLMDBENCH_VLLM_INFRA|^LLMDBENCH_VLLM_GAIE|^LLMDBENCH_LLMD_IMAGE"
        export LLMDBENCH_HARNESS_STACK_TYPE=llm-d
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get gateway --no-headers -l stood-up-via=${LLMDBENCH_DEPLOY_METHODS} | awk '{print $1}')
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=80
      fi

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 0 && $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 0 ]]; then
        export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="LLMDBENCH_BASE64_CONTEXT_CONTENTS|^LLMDBENCH_VLLM_COMMON_NAMESPACE|^LLMDBENCH_DEPLOY_CURRENT"
        announce "⚠️ Deployment method - $LLMDBENCH_DEPLOY_METHODS - is neither \"standalone\" nor \"modelservice\". "

        announce "🔍 Trying to find a matching endpoint name..."
        export LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers | awk '{print $1}' | grep ${LLMDBENCH_DEPLOY_METHODS} || true)
        if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
          export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json | jq -r '.spec.ports[] | select(.name == "default") | .port')
        else
          export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod --no-headers | awk '{print $1}' | grep ${LLMDBENCH_DEPLOY_METHODS} | head -n 1 || true)
          export LLMDBENCH_VLLM_FQDN=
          if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
            announce "ℹ️ Stack Endpoint name detected is \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME\""
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json | jq -r ".spec.containers[0].ports[0].containerPort")
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json | jq -r ".status.podIP")
          fi
        fi
        export LLMDBENCH_DEPLOY_CURRENT_MODEL="auto"
      fi

      if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
        export LLMDBENCH_HARNESS_STACK_TYPE=mock
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=mock
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=1234
      fi

      if [[ -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
        announce "❌ ERROR: could not find an endpoint name for a stack deployed via method \"$LLMDBENCH_DEPLOY_METHODS\" (i.e., with label \"stood-up-via=$LLMDBENCH_DEPLOY_METHODS\")"
        announce "📌 Tip: If the llm-d stack you're trying to benchmark was NOT deployed via \"standup.sh\", just use \"run.sh -t <string that matches the service/gateway name>\""

        exit 1
      fi

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_URL="http://${LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME}${LLMDBENCH_VLLM_FQDN}:${LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT}/${LLMDBENCH_DEPLOY_CURRENT_MODELID}"
      else
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_URL="http://${LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME}${LLMDBENCH_VLLM_FQDN}:${LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT}"
      fi
      announce "ℹ️ Stack Endpoint URL detected is \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_URL\""

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 0 && $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 0 ]]; then
        announce "🔍 Trying to find a matching hugging face token (hf.*token*.)..."
        export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get secrets --no-headers | grep -E .*hf.*token.* | awk '{print $1}')
        if [[ ! -z $LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME ]]; then
          announce "ℹ️ Hugging face token detected is \"$LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME\""
        else
          announce "❌ ERROR: could not find a hugging face token"
          exit 1
        fi
      fi

      announce "🔍 Trying to detect the model name served by the stack ($LLMDBENCH_HARNESS_STACK_ENDPOINT_URL)..."
      if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
        announce "ℹ️ Stack model detected is \"mock\""
      else

        set +euo pipefail
        received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL} NA)
        set -euo pipefail

        if [[ $LLMDBENCH_DEPLOY_CURRENT_MODEL == "auto" ]]; then
          if [[ -z $received_model_name ]]; then
            announce "❌ Unable to detect stack model!"
            exit 1
          fi

          export LLMDBENCH_DEPLOY_CURRENT_MODEL=$received_model_name
          export LLMDBENCH_DEPLOY_CURRENT_MODELID=$(model_attribute $LLMDBENCH_DEPLOY_CURRENT_MODEL modelid)

          announce "ℹ️ Stack model detected is \"$received_model_name\""
        elif [[ ${received_model_name} == ${LLMDBENCH_DEPLOY_CURRENT_MODEL} ]]; then
          announce "ℹ️ Stack model detected is \"$received_model_name\", matches requested \"$LLMDBENCH_DEPLOY_CURRENT_MODEL\""
        else
          announce "❌ Stack model detected is \"$received_model_name\" (instead of $LLMDBENCH_DEPLOY_CURRENT_MODEL)!"
          exit 1
        fi
      fi

      if [[ $LLMDBENCH_HARNESS_DEBUG -eq 1 ]]; then
        render_workload_templates all

        export LLMDBENCH_RUN_EXPERIMENT_HARNESS="sleep infinity"
        export LLMDBENCH_RUN_EXPERIMENT_ANALYZER="sleep infinity"

      else

        generate_profile_parameter_treatments ${LLMDBENCH_HARNESS_NAME} ${LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS}

        workload_template_full_path=$(find ${LLMDBENCH_MAIN_DIR}/workload/profiles/${LLMDBENCH_HARNESS_NAME}/ | grep ${LLMDBENCH_HARNESS_EXPERIMENT_PROFILE} | head -n 1 || true)
        if [[ -z $workload_template_full_path ]]; then
          announce "❌ Could not find workload template \"$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE\" inside directory \"${LLMDBENCH_MAIN_DIR}/workload/profiles/${LLMDBENCH_HARNESS_NAME}/\" (variable $LLMDBENCH_HARNESS_EXPERIMENT_PROFILE)"
          exit 1
        fi

        render_workload_templates ${LLMDBENCH_HARNESS_EXPERIMENT_PROFILE}
        export LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST=$LLMDBENCH_HARNESS_NAME

        export LLMDBENCH_RUN_EXPERIMENT_HARNESS=$(find ${LLMDBENCH_MAIN_DIR}/workload/harnesses -name ${LLMDBENCH_HARNESS_NAME}* | rev | cut -d '/' -f1 | rev)
        export LLMDBENCH_RUN_EXPERIMENT_ANALYZER=$(find ${LLMDBENCH_MAIN_DIR}/analysis/ -name ${LLMDBENCH_HARNESS_NAME}* | rev | cut -d '/' -f1 | rev)

      fi

      for workload_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete configmap $workload_type-profiles --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} create configmap $workload_type-profiles --from-file=${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/${workload_type}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      done

      export LLMDBENCH_RUN_EXPERIMENT_ID_PREFIX=""
      for treatment in $(ls ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/${workload_type}/*.yaml); do

        export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=$(echo $treatment | rev | cut -d '/' -f 1 | rev)
        export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE=$(echo $treatment | rev | cut -d '/' -f 1 | rev)

        tf=$(cat ${treatment} | grep "#treatment" | tail -1 | $LLMDBENCH_CONTROL_SCMD 's/^#//' || true)
        if [[ -f ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/${workload_type}/treatment_list/$tf ]]; then
          tid=$(sed -e 's/[^[:alnum:]][^[:alnum:]]*/_/g' <<<"${tf%.txt}")   # remove non alphanumeric and .txt
          tid=${tid#treatment_}
          if [ -z "${LLMDBENCH_RUN_EXPERIMENT_ID}" ]; then
            export LLMDBENCH_RUN_EXPERIMENT_ID=$(date +%s)-${tid}
          else
            if [[ -z $LLMDBENCH_RUN_EXPERIMENT_ID_PREFIX ]]; then
              export LLMDBENCH_RUN_EXPERIMENT_ID_PREFIX=$LLMDBENCH_RUN_EXPERIMENT_ID
            fi
            export LLMDBENCH_RUN_EXPERIMENT_ID=${LLMDBENCH_RUN_EXPERIMENT_ID_PREFIX}-${tid}
          fi

          echo
          cat ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/${workload_type}/treatment_list/$tf | grep -v ^1i# | cut -d '^' -f 3
          echo
        fi

        export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX=${LLMDBENCH_HARNESS_NAME}_${LLMDBENCH_RUN_EXPERIMENT_ID}_${LLMDBENCH_HARNESS_STACK_NAME}
        export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX/$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX

        local_results_dir=${LLMDBENCH_CONTROL_WORK_DIR}/results/${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX}
        local_analysis_dir=${LLMDBENCH_CONTROL_WORK_DIR}/analysis/${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX}
        if [[ -f ${local_analysis_dir}/summary.txt ]]; then
          announce "⏭️  This particular workload profile was already executed against this stack. Please remove \"${local_analysis_dir}/summary.txt\" to re-execute".
          continue
        fi

        if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
          announce "ℹ️ Skipping \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" creation"
          mkdir -p ${local_results_dir}
          mkdir -p ${local_analysis_dir}
        else
          if [[ "$LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE" == /* ]]; then
            potential_gaie_path=$(echo $LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE'.yaml' | $LLMDBENCH_CONTROL_SCMD 's^.yaml.yaml^.yaml^g')
          else
            potential_gaie_path=$(echo ${LLMDBENCH_MAIN_DIR}/setup/presets/gaie/$LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE'.yaml' | $LLMDBENCH_CONTROL_SCMD 's^.yaml.yaml^.yaml^g')
          fi

          if [[ -f $potential_gaie_path ]]; then
            export LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS_CONFIG=$potential_gaie_path
          fi

          export LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD="^$(echo $LLMDBENCH_HARNESS_ENVVARS_TO_YAML | $LLMDBENCH_CONTROL_SCMD -e 's/,/|^/g' -e 's/$/|^/g')$LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD"

          create_harness_pod

          announce "🚀 Starting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" ($LLMDBENCH_DEPLOY_CURRENT_MODEL)..."
          llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
          announce "✅ Pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" started"

          announce "⏳ Waiting for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
          llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod -l app=${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
          announce "✅ Benchmark execution for model \"$model\" effectivelly started"

          announce "ℹ️ You can follow the execution's output with \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} logs -l app=${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} -f\"..."

          LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pod -l role=llm-d-benchmark-data-access --no-headers -o name | $LLMDBENCH_CONTROL_SCMD 's|^pod/||g')
          llmdbench_execute_cmd "mkdir -p ${local_results_dir}/ && mkdir -p ${local_analysis_dir}/" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

          copy_results_cmd="${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} cp --retries=5 $LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME:${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR} ${local_results_dir}"
          copy_analysis_cmd="rsync -az --inplace --delete ${local_results_dir}/analysis/ ${local_analysis_dir}/ && rm -rf ${local_results_dir}/analysis"

          if [[ $LLMDBENCH_HARNESS_DEBUG -eq 0 && ${LLMDBENCH_HARNESS_WAIT_TIMEOUT} -ne 0 ]]; then
            announce "⏳ Waiting for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be in \"Completed\" state (timeout=${LLMDBENCH_HARNESS_WAIT_TIMEOUT}s)..."
            llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} wait --timeout=${LLMDBENCH_HARNESS_WAIT_TIMEOUT}s --for=condition=ready=False pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
            announce "✅ Benchmark execution for model \"$model\" completed"

            is_pod_in_error=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pod/${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} --no-headers | grep " Error " || true)
            if [ ! -z $is_pod_in_error ]; then
              announce "❌ Final status of pod \"$LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME\" is \"Error\""
              exit 1
            fi

            announce "🗑️ Deleting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" ..."
            llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
            announce "✅ Pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" deleted"

            announce "🏗️ Collecting results for model \"$model\" ($LLMDBENCH_DEPLOY_CURRENT_MODEL) to \"${local_results_dir}\"..."
            llmdbench_execute_cmd "${copy_results_cmd}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

            if [[ -d ${local_results_dir}/analysis && $LLMDBENCH_HARNESS_DEBUG -eq 0 && ${LLMDBENCH_HARNESS_WAIT_TIMEOUT} -ne 0 ]]; then
              llmdbench_execute_cmd "$copy_analysis_cmd" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
            fi

            announce "✅ Results for model \"$model\" collected successfully"
          elif [[ $LLMDBENCH_HARNESS_WAIT_TIMEOUT -eq 0 ]]; then
            announce "ℹ️ Harness was started with LLMDBENCH_HARNESS_WAIT_TIMEOUT=0. Will NOT wait for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be in \"Completed\" state. The pod can be accessed through \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} exec -it pod/${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} -- bash\""
            announce "ℹ️ To collect results after an execution, \"$copy_results_cmd && $copy_analysis_cmd"
            break
          else
            announce "ℹ️ Harness was started in \"debug mode\". The pod can be accessed through \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} exec -it pod/${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} -- bash\""
            announce "ℹ️ In order to execute a given workload profile, run \"llm-d-benchmark.sh <[$(get_harness_list)]> [WORKLOAD FILE NAME]\" (all inside the pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\")"
            announce "ℹ️ To collect results after an execution, \"$copy_results_cmd && $copy_analysis_cmd"
            break
          fi
        fi
      done
    fi

    if [[ $LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY -eq 1 ]]; then
      announce "🔍 Analyzing collected data..."
      conda_root="$(conda info --all --json | jq -r '.root_prefix'  2>/dev/null)"
      if [ "$LLMDBENCH_CONTROL_DEPLOY_HOST_OS" = "mac" ]; then
        conda_sh="${conda_root}/base/etc/profile.d/conda.sh"
      else
        conda_sh="${conda_root}/etc/profile.d/conda.sh"
      fi
      if [ -f "${conda_sh}" ]; then
        llmdbench_execute_cmd "source \"${conda_sh}\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      else
        announce "❌ Could not find conda.sh for $LLMDBENCH_CONTROL_DEPLOY_HOST_OS. Please verify your Anaconda installation."
        exit 1
      fi

      llmdbench_execute_cmd "conda activate \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_PCMD} $LLMDBENCH_MAIN_DIR/analysis/analyze_results.py" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Data analysis done."
    fi
    unset LLMDBENCH_DEPLOY_CURRENT_MODEL

  done
done
