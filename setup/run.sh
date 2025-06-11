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

function show_usage {
    echo -e "Usage: $(echo $0 | rev | cut -d '/' -f 1 | rev) -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
             -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO) ] \n \
             -m/--models [list the models to be run against (default=$LLMDBENCH_DEPLOY_MODEL_LIST) ] \n \
             -z/--skip [skip the execution of the experiment, and only collect data (default=$LLMDBENCH_HARNESS_SKIP_RUN) ] \n \
             -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE) ] \n \
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
        -z|--skip)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_SKIP_RUN=1
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

export LLMDBENCH_BASE64_CONTEXT=$(cat $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx | base64)

create_fmperf_harness_pod() {
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
    command: ["llm-d-benchmark.sh"]
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
      value: "/requests/${LLMDBENCH_FMPERF_STACK_NAME}/"
    - name: LLMDBENCH_BASE64_CONTEXT
      value: "$LLMDBENCH_BASE64_CONTEXT"
    - name: LLMDBENCH_BASE64_FMPERF_WORKLOAD
      value: "${LLMDBENCH_BASE64_FMPERF_WORKLOAD}"
    - name: LLMDBENCH_HARNESS_NAMESPACE
      value: "${LLMDBENCH_HARNESS_NAMESPACE}"
    - name: LLMDBENCH_FMPERF_STACK_TYPE
      value: "${LLMDBENCH_FMPERF_STACK_TYPE}"
    - name: LLMDBENCH_FMPERF_ENDPOINT_URL
      value: "${LLMDBENCH_FMPERF_ENDPOINT_URL}"
    - name: LLMDBENCH_FMPERF_STACK_NAME
      value: "$LLMDBENCH_FMPERF_STACK_NAME"
    - name: HF_TOKEN_SECRET
      value: "${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}"
    volumeMounts:
    - name: results
      mountPath: /requests
  volumes:
  - name: results
    persistentVolumeClaim:
      claimName: $LLMDBENCH_HARNESS_PVC_NAME
  restartPolicy: Never
EOF
}

create_inference_perf_harness_pod() {
  cat <<EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: inference-perf-config
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
data:
  config.yaml: |
$(cat "${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/${LLMDBENCH_HARNESS_EXPERIMENT_PROFILE}" | sed 's/^/    /')
---
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
    - "inference-perf --config_file /etc/config/config.yaml && mkdir -p \"/requests/\$LLMDBENCH_FMPERF_STACK_NAME\" && find /workspace -name '*.json' -exec mv -t \"/requests/\$LLMDBENCH_FMPERF_STACK_NAME\"/ {} +"
    env:
    - name: LLMDBENCH_FMPERF_ENDPOINT_URL
      value: "${LLMDBENCH_FMPERF_ENDPOINT_URL}"
    - name: LLMDBENCH_FMPERF_STACK_NAME
      value: "${LLMDBENCH_FMPERF_STACK_NAME}"
    - name: HUGGING_FACE_HUB_TOKEN
      valueFrom:
        secretKeyRef:
          name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
          key: HF_TOKEN
    volumeMounts:
    - name: results
      mountPath: /requests
    - name: config-volume
      mountPath: /etc/config
      readOnly: true
  volumes:
  - name: results
    persistentVolumeClaim:
      claimName: $LLMDBENCH_HARNESS_PVC_NAME
  - name: config-volume
    configMap:
      name: inference-perf-config
  restartPolicy: Never
EOF
}

for method in ${LLMDBENCH_DEPLOY_METHODS//,/ }; do

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do

    export LLMDBENCH_FMPERF_STACK_NAME=$(echo ${method} | $LLMDBENCH_CONTROL_SCMD 's/deployer/llm-d/g')-$(model_attribute $model parameters)-$(model_attribute $model type)

    export LLMDBENCH_DEPLOY_CURRENT_MODEL=$(model_attribute $model model)

    if [[ $LLMDBENCH_HARNESS_SKIP_RUN -eq 1 ]]; then
      announce "⏭️ Command line option \"-z\--skip\" invoked. Will skip experiment execution (and move straight to analysis)"
    else
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete job lmbenchmark-evaluate-${LLMDBENCH_FMPERF_STACK_NAME} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

      if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
        export LLMDBENCH_FMPERF_STACK_TYPE=vllm-prod
        export LLMDBENCH_FMPERF_ENDPOINT_URL="http://"$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers | grep standalone | awk '{print $1}' || true).${LLMDBENCH_VLLM_COMMON_NAMESPACE}.svc.cluster.local
      else
        export LLMDBENCH_FMPERF_STACK_TYPE=llm-d
        export LLMDBENCH_FMPERF_ENDPOINT_URL="http://"$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get gateway --no-headers | tail -n1 | awk '{print $1}').${LLMDBENCH_VLLM_COMMON_NAMESPACE}.svc.cluster.local
      fi

      render_template ${LLMDBENCH_MAIN_DIR}/workload/profiles/$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE.in ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE

      export LLMDBENCH_BASE64_FMPERF_WORKLOAD=$(cat ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE | base64)

      if [[ "${LLMDBENCH_HARNESS_NAME}" == "fmperf" ]]; then
        create_fmperf_harness_pod
      elif [[ "${LLMDBENCH_HARNESS_NAME}" == "inference-perf" ]]; then
        create_inference_perf_harness_pod
      else
        announce "❌ Unknown LLMDBENCH_HARNESS_NAME: ${LLMDBENCH_HARNESS_NAME}. Cannot create benchmark pod."
        exit 1
      fi

      announce "🚀 Starting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" ($LLMDBENCH_DEPLOY_CURRENT_MODEL)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" started"

      announce "⏳ Waiting for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod -l app=${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Benchmark execution for model \"$model\" effectivelly started"

      announce "ℹ️ You can follow the execution's output with \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} logs -l app=${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} -f\"..."

      announce "⏳ Waiting for pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" to be in \"Completed\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=ready=False pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Benchmark execution for model \"$model\" completed"

      announce "🗑️ Deleting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\" ..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" for model \"$model\""

      announce "🏗️  Collecting results for model \"$model\" ($LLMDBENCH_DEPLOY_CURRENT_MODEL) to \"${LLMDBENCH_CONTROL_WORK_DIR}/results/\"..."
      LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME=$(llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pod -l app=llm-d-benchmark-harness --no-headers -o name" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0 | $LLMDBENCH_CONTROL_SCMD 's|^pod/||g')
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} cp $LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME:/requests/${LLMDBENCH_FMPERF_STACK_NAME}/ ${LLMDBENCH_CONTROL_WORK_DIR}/results/" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Results for model \"$model\" collected successfully"
    fi

    if [[ $LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY -eq 1 ]]; then
      announce "🔍 Analyzing collected data..."
      if [ "$LLMDBENCH_CONTROL_DEPLOY_HOST_OS" = "mac" ] && [ -f "/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh" ]; then
        llmdbench_execute_cmd "source \"/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      elif [ "$LLMDBENCH_CONTROL_DEPLOY_HOST_OS" = "linux" ] && [ -f "/opt/miniconda/etc/profile.d/conda.sh" ]; then
        llmdbench_execute_cmd "source \"/opt/miniconda/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      else
        echo "❌ Could not find conda.sh for $LLMDBENCH_CONTROL_DEPLOY_HOST_OS. Please verify your Anaconda installation."
        exit 1
      fi

      llmdbench_execute_cmd "conda activate \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_PCMD} $LLMDBENCH_MAIN_DIR/analysis/analyze_results.py" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Data analysis done."
    fi

    if [[ -d ${LLMDBENCH_CONTROL_WORK_DIR}/results/analysis ]]; then
      llmdbench_execute_cmd "mv ${LLMDBENCH_CONTROL_WORK_DIR}/results/analysis ${LLMDBENCH_CONTROL_WORK_DIR}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi

    unset LLMDBENCH_DEPLOY_CURRENT_MODEL

  done
done