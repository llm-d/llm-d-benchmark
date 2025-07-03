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
export LLMDBENCH_HARNESS_SKIP_RUN=0
export LLMDBENCH_HARNESS_DEBUG=0

function get_harness_list {
  ls ${LLMDBENCH_MAIN_DIR}/workload/harnesses | $LLMDBENCH_CONTROL_SCMD 's^inference-perf^inference_perf^' | cut -d '-' -f 1 | $LLMDBENCH_CONTROL_SCMD -n -e 's^inference_perf^inference-perf^' -e 'H;${x;s/\n/,/g;s/^,//;p;}'
}

function show_usage {
    echo -e "Usage: $(echo $0 | rev | cut -d '/' -f 1 | rev) -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
             -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO)] \n \
             -m/--models [list the models to be run against (default=$LLMDBENCH_DEPLOY_MODEL_LIST)] \n \
             -p/--namespace [namespace where to deploy (default=$LLMDBENCH_VLLM_COMMON_NAMESPACE)] \n \
             -t/--methods [eployment method (default=$LLMDBENCH_DEPLOY_METHODS, possible values \"standalone\", \"deployer\" or any other string - pod name or service name - matching a resource on cluster)] \n \
             -l/--harness [harness used to generate load (default=$LLMDBENCH_HARNESS_NAME, possible values $(get_harness_list)] \n \
             -w/--workload [workload to be used by the harness (default=$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE, possible values (check \"workload/profiles\" dir)] \n \
             -k/--pvc [name of the PVC used to store the results (default=$LLMDBENCH_HARNESS_PVC_NAME)] \n \
             -z/--skip [skip the execution of the experiment, and only collect data (default=$LLMDBENCH_HARNESS_SKIP_RUN)] \n \
             -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE)] \n \
             -s/--wait [time to wait until the benchmark run is complete (default=$LLMDBENCH_HARNESS_WAIT_TIMEOUT, value \"0\" means "do not wait\""] \n \
             -d/--debug [execute harness in \"debug-mode\" (default=$LLMDBENCH_HARNESS_DEBUG)] \n \
             -h/--help (show this help) \n\

              * [models] can be specified with a full name (e.g., \"ibm-granite/granite-3.3-2b-instruct\") or as an alias. The following aliases are available \n\
                  - llama-3b -> meta-llama/Llama-3.2-3B-Instruct \n\
                  - llama-8b -> meta-llama/Llama-3.1-8B-Instruct \n\
                  - llama-17b -> RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic \n\
                  - llama-70b -> meta-llama/Llama-3.1-70B-Instruct"
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
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE=$(echo $key | cut -d '=' -f 2)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE=$(echo $key | cut -d '=' -f 2)
        ;;
        -p|--namespace)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE="$2"
        export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE="$2"
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
        -t=*|--methods=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS=$(echo $key | cut -d '=' -f 2)
        ;;
        -t|--methods)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS="$2"
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

export LLMDBENCH_EXPERIMENT_ID=${LLMDBENCH_EXPERIMENT_ID:-"default"}

export LLMDBENCH_BASE64_CONTEXT=$(cat $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx | base64 $LLMDBENCH_BASE64_ARGS)

create_harness_pod() {
  cat <<EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
apiVersion: v1
kind: Pod
metadata:
  name: ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
  labels:
    app: ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}
spec:
  serviceAccountName: $LLMDBENCH_HARNESS_SERVICE_ACCOUNT
  containers:
  - name: harness
    image: ${LLMDBENCH_IMAGE_REGISTRY}/${LLMDBENCH_IMAGE_REPO}:${LLMDBENCH_IMAGE_TAG}
    imagePullPolicy: Always
    command: ["sh", "-c"]
    args:
    - "${LLMDBENCH_HARNESS_EXECUTABLE}"
    env:
    - name: LLMDBENCH_RUN_EXPERIMENT_LAUNCHER
      value: "1"
    - name: LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY
      value: "${LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY}"
    - name: LLMDBENCH_RUN_EXPERIMENT_HARNESS
      value: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS}"
    - name: LLMDBENCH_RUN_EXPERIMENT_ANALYZER
      value: "${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}"
    - name: LLMDBENCH_CONTROL_WORK_DIR
      value: "/requests/${LLMDBENCH_HARNESS_STACK_NAME}/"
    - name: LLMDBENCH_BASE64_CONTEXT
      value: "$LLMDBENCH_BASE64_CONTEXT"
    - name: LLMDBENCH_BASE64_HARNESS_WORKLOAD
      value: "${LLMDBENCH_BASE64_HARNESS_WORKLOAD}"
    - name: LLMDBENCH_HARNESS_NAMESPACE
      value: "${LLMDBENCH_HARNESS_NAMESPACE}"
    - name: LLMDBENCH_HARNESS_STACK_TYPE
      value: "${LLMDBENCH_HARNESS_STACK_TYPE}"
    - name: LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
      value: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL}"
    - name: LLMDBENCH_HARNESS_STACK_NAME
      value: "$LLMDBENCH_HARNESS_STACK_NAME"
    - name: HF_TOKEN_SECRET
      value: "${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}"
    - name: HUGGING_FACE_HUB_TOKEN
      valueFrom:
        secretKeyRef:
          name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
          key: HF_TOKEN
EOF

is_pvc=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pvc --ignore-not-found | grep ${LLMDBENCH_HARNESS_PVC_NAME} || true)
if [[ ! -z ${is_pvc} ]]; then
  cat <<EOF >> $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
    volumeMounts:
    - name: results
      mountPath: /requests
  volumes:
  - name: results
    persistentVolumeClaim:
      claimName: $LLMDBENCH_HARNESS_PVC_NAME
EOF
fi
  cat <<EOF >> $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
  restartPolicy: Never
EOF
}

function cleanup_pre_execution {
  announce "🗑️ Deleting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\"..."
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete job lmbenchmark-evaluate-${LLMDBENCH_HARNESS_STACK_NAME} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  announce "ℹ️ Done deleting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" (it will be now recreated)"
}

export LLMDBENCH_CURRENT_STEP=05
source ${LLMDBENCH_STEPS_DIR}/05_ensure_harness_namespace_prepared.sh > /dev/null 2>&1
if [[ $? -ne 0 ]]; then
  announce "❌ Error while attempting to setup the harness namespace"
  exit 1
fi
unset LLMDBENCH_CURRENT_STEP

for method in ${LLMDBENCH_DEPLOY_METHODS//,/ }; do

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do

    export LLMDBENCH_HARNESS_STACK_NAME=$(echo ${method} | $LLMDBENCH_CONTROL_SCMD 's^deployer^llm-d^g')-$(model_attribute $model parameters)-$(model_attribute $model type)

    export LLMDBENCH_DEPLOY_CURRENT_MODEL=$(model_attribute $model model)

    if [[ $LLMDBENCH_HARNESS_SKIP_RUN -eq 1 ]]; then
      announce "⏭️ Command line option \"-z\--skip\" invoked. Will skip experiment execution (and move straight to analysis)"
    else
      cleanup_pre_execution

      export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=
      export LLMDBENCH_VLLM_COMMON_FQDN=".${LLMDBENCH_VLLM_COMMON_NAMESPACE}${LLMDBENCH_VLLM_COMMON_FQDN}"

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
        export LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers | grep standalone | awk '{print $1}' || true)
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=80
      fi

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_DEPLOYER_ACTIVE -eq 1 ]]; then
        export LLMDBENCH_HARNESS_STACK_TYPE=llm-d
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get gateway --no-headers | tail -n1 | awk '{print $1}')
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=80
      fi

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_DEPLOYER_ACTIVE -eq 0 && $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 0 ]]; then
        announce "🔍 Deployment method - $LLMDBENCH_DEPLOY_METHODS - is neither \"standalone\" nor \"deployer\". Trying to find a matching endpoint name..."
        export LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod
        export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers | grep ${LLMDBENCH_DEPLOY_METHODS} | awk '{print $1}' || true)
        if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
          export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service/$LLMDBENCH_HARNESS_STACK_SERVICE_NAME --no-headers -o json | jq -r '.spec.ports[0].port')
        else
          export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod --no-headers | grep ${LLMDBENCH_DEPLOY_METHODS} | head -n 1 | awk '{print $1}' || true)
          export LLMDBENCH_VLLM_COMMON_FQDN=
          if [[ ! -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
            announce "ℹ️ Stack Endpoint name detected is \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME\""
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json | jq -r ".spec.containers[0].ports[0].containerPort")
            export LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pod/$LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME --no-headers -o json | jq -r ".status.podIP")
          fi
        fi
      fi

      if [[ -z $LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME ]]; then
        announce "❌ ERROR: could not find an endpoint name for a stack deployed via method \"$LLMDBENCH_DEPLOY_METHODS\""
        exit 1
      fi

      export LLMDBENCH_HARNESS_STACK_ENDPOINT_URL="http://${LLMDBENCH_HARNESS_STACK_ENDPOINT_NAME}${LLMDBENCH_VLLM_COMMON_FQDN}:${LLMDBENCH_HARNESS_STACK_ENDPOINT_PORT}"
      announce "ℹ️ Stack Endpoint URL detected is \"$LLMDBENCH_HARNESS_STACK_ENDPOINT_URL\""

      if [[ $LLMDBENCH_HARNESS_DEBUG -eq 0 ]]; then
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS=$(find ${LLMDBENCH_MAIN_DIR}/workload/harnesses -name ${LLMDBENCH_HARNESS_NAME}* | rev | cut -d '/' -f1 | rev)
        export LLMDBENCH_RUN_EXPERIMENT_ANALYZER=$(find ${LLMDBENCH_MAIN_DIR}/analysis/ -name ${LLMDBENCH_HARNESS_NAME}* | rev | cut -d '/' -f1 | rev)
      else
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS="sleep infinity"
        export LLMDBENCH_RUN_EXPERIMENT_ANALYZER="sleep infinity"
      fi

      workload_template_full_path=$(find ${LLMDBENCH_MAIN_DIR}/workload/profiles/${LLMDBENCH_HARNESS_NAME}/ | grep ${LLMDBENCH_HARNESS_EXPERIMENT_PROFILE} | head -n 1 || true)
      if [[ -z $workload_template_full_path ]]; then
        announce "❌ Could not find workload template \"$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE\" inside directory \"${LLMDBENCH_MAIN_DIR}/workload/profiles/${LLMDBENCH_HARNESS_NAME}/\" (variable $LLMDBENCH_HARNESS_EXPERIMENT_PROFILE)"
        exit 1
      fi

      workload_template_file_name=$(echo ${workload_template_full_path} | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_CONTROL_SCMD "s^\.in$^^g")

      announce "ℹ️ Selected workload profile is \"$workload_template_file_name\""
      render_template $workload_template_full_path ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$workload_template_file_name
      export LLMDBENCH_BASE64_HARNESS_WORKLOAD=$(cat ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$workload_template_file_name | base64 $LLMDBENCH_BASE64_ARGS)

      create_harness_pod

      announce "🚀 Starting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" ($LLMDBENCH_DEPLOY_CURRENT_MODEL)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" started"

      announce "⏳ Waiting for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod -l app=${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Benchmark execution for model \"$model\" effectivelly started"

      announce "ℹ️ You can follow the execution's output with \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} logs -l app=${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} -f\"..."

      LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME=$(llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pod -l app=llm-d-benchmark-harness --no-headers -o name" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0 | $LLMDBENCH_CONTROL_SCMD 's|^pod/||g')

      llmdbench_execute_cmd "mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/results/$LLMDBENCH_HARNESS_STACK_NAME/ && mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/analysis/$LLMDBENCH_HARNESS_STACK_NAME/" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

      copy_results_cmd="${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} cp $LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME:/requests/${LLMDBENCH_HARNESS_STACK_NAME}/ ${LLMDBENCH_CONTROL_WORK_DIR}/results/${LLMDBENCH_HARNESS_STACK_NAME}"
      copy_analisys_cmd="rsync -az --inplace --delete ${LLMDBENCH_CONTROL_WORK_DIR}/results/$LLMDBENCH_HARNESS_STACK_NAME/analysis/ ${LLMDBENCH_CONTROL_WORK_DIR}/analysis/${LLMDBENCH_HARNESS_STACK_NAME} && rm -rf ${LLMDBENCH_CONTROL_WORK_DIR}/results/$LLMDBENCH_HARNESS_STACK_NAME/analysis"

      if [[ $LLMDBENCH_HARNESS_DEBUG -eq 0 && ${LLMDBENCH_HARNESS_WAIT_TIMEOUT} -ne 0 ]]; then
        announce "⏳ Waiting for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be in \"Completed\" state (timeout=${LLMDBENCH_HARNESS_WAIT_TIMEOUT}s)..."
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} wait --timeout=${LLMDBENCH_HARNESS_WAIT_TIMEOUT}s --for=condition=ready=False pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ Benchmark execution for model \"$model\" completed"

        announce "🗑️ Deleting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" ..."
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ Pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\""

        announce "🏗️  Collecting results for model \"$model\" ($LLMDBENCH_DEPLOY_CURRENT_MODEL) to \"${LLMDBENCH_CONTROL_WORK_DIR}/results/${LLMDBENCH_HARNESS_STACK_NAME}\"..."
        llmdbench_execute_cmd "${copy_results_cmd}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ Results for model \"$model\" collected successfully"
      elif [[ $LLMDBENCH_HARNESS_WAIT_TIMEOUT -eq 0 ]]; then
        announce "ℹ️ Harness was started with LLMDBENCH_HARNESS_WAIT_TIMEOUT=0. Will NOT wait for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be in \"Completed\" state. The pod can be accessed through \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} exec -it pod/${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} -- bash\""
        announce "ℹ️ To collect results after an execution, \"$copy_results_cmd && $copy_analisys_cmd"
      else
        announce "ℹ️ Harness was started in \"debug mode\". The pod can be accessed through \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} exec -it pod/${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} -- bash\""
        announce "ℹ️ In order to execute a given workload profile, change the value of env var \"LLMDBENCH_RUN_EXPERIMENT_HARNESS\" and \"LLMDBENCH_RUN_EXPERIMENT_ANALYZER\", follwing by running \"llm-d-benchmark.sh\" (all inside the pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\")"
        announce "ℹ️ To collect results after an execution, \"$copy_results_cmd && $copy_analisys_cmd"
      fi
    fi

    if [[ $LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY -eq 1 ]]; then
      announce "🔍 Analyzing collected data..."
      if [ "$LLMDBENCH_CONTROL_DEPLOY_HOST_OS" = "mac" ] && [ -f "/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh" ]; then
        llmdbench_execute_cmd "source \"/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      elif [ "$LLMDBENCH_CONTROL_DEPLOY_HOST_OS" = "linux" ] && [ -f "/opt/miniconda/etc/profile.d/conda.sh" ]; then
        llmdbench_execute_cmd "source \"/opt/miniconda/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      else
        announce "❌ Could not find conda.sh for $LLMDBENCH_CONTROL_DEPLOY_HOST_OS. Please verify your Anaconda installation."
        exit 1
      fi

      llmdbench_execute_cmd "conda activate \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_PCMD} $LLMDBENCH_MAIN_DIR/analysis/analyze_results.py" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Data analysis done."
    fi

    if [[ -d ${LLMDBENCH_CONTROL_WORK_DIR}/results/$LLMDBENCH_HARNESS_STACK_NAME/analysis && $LLMDBENCH_HARNESS_DEBUG -eq 0 && ${LLMDBENCH_HARNESS_WAIT_TIMEOUT} -ne 0 ]]; then
      llmdbench_execute_cmd "$copy_analisys_cmd" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi

    unset LLMDBENCH_DEPLOY_CURRENT_MODEL

  done
done
