#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
  
  # make sure llm-d-modelservice helm repo is available
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo add ${LLMDBENCH_VLLM_MODELSERVICE_CHART_NAME} ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY_URL} --force-update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  if [[ $LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION == "auto" ]]; then
    export LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION=$($LLMDBENCH_CONTROL_HCMD search repo ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY} | tail -1 | awk '{print $2}' || true)
    if [[ -z $LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION ]]; then
      announce "❌ Unable to find a version for model service helm chart!"
    fi
  fi

  llmdbench_execute_cmd "mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  model_number=0
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    export LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL=$(model_attribute $model modelid_label)

    llmdbench_execute_cmd "printf -v MODEL_NUM \"%02d\" \"$model_number\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    llmdbench_execute_cmd "mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/${MODEL_NUM}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/helmfile-${MODEL_NUM}.yaml
repositories:
  - name: ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY}
    url: https://llm-d-incubation.github.io/llm-d-modelservice/

releases:
  - name: infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
    chart: ${LLMDBENCH_VLLM_INFRA_CHART_NAME}
    version: ${LLMDBENCH_VLLM_INFRA_CHART_VERSION}
    installed: true
    labels:
      managedBy: llm-d-infra-installer

  - name: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}-${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}-ms
    namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
    chart: ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY}/${LLMDBENCH_VLLM_MODELSERVICE_CHART_NAME}
    version: ${LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION}
    installed: true
    needs:
      -  ${LLMDBENCH_VLLM_COMMON_NAMESPACE}/infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    values:
      - ${MODEL_NUM}/ms-values.yaml
    labels:
      managedBy: helmfile

  - name: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}-${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}-gaie
    namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
    chart: ${LLMDBENCH_VLLM_GAIE_CHART_NAME}
    version: ${LLMDBENCH_VLLM_GAIE_CHART_VERSION}
    installed: true
    needs:
      -  ${LLMDBENCH_VLLM_COMMON_NAMESPACE}/infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    values:
      - ${MODEL_NUM}/gaie-values.yaml
    labels:
      managedBy: helmfile
EOF

    ((model_number++))
  done
  announce "✅ Completed gaie deployment"
else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
