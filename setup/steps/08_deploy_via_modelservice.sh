#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
  extract_environment

  # make sure llm-d-modelservice helm repo is available
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo add ${LLMDBENCH_VLLM_MODELSERVICE_CHART} ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY_URL} --force-update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0

  # deploy models
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    helm_opts="--version ${LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --values ${LLMDBENCH_VLLM_MODELSERVICE_VALUES_FILE} ${LLMDBENCH_VLLM_MODELSERVICE_ADDITIONAL_SETS}"
    announce "🚀 Calling helm upgrade --install with options \"${helm_opts}\"..."
    llmdbench_execute_cmd "export HF_TOKEN=$LLMDBENCH_HF_TOKEN; $LLMDBENCH_CONTROL_HCMD upgrade --install $model ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY}/${LLMDBENCH_VLLM_MODELSERVICE_CHART} $helm_opts" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
    announce "✅ helm upgrade completed successfully"
  done # for model in ...
else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
