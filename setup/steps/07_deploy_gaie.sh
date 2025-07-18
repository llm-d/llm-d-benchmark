#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
  extract_environment

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do

    llmdbench_execute_cmd "mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/gaie-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    cat << EOF > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/gaie-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/values.yaml
inferenceExtension:
  replicas: 1
  image:
    name: ${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_NAME}
    hub: ${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REGISTRY}/${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REPO}
    tag: $(get_image ${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REGISTRY} ${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REPO} ${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_NAME} ${LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG} 1)
    pullPolicy: Always
  extProcPort: 9002
  pluginsConfigFile: "${LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS}"

  # using upstream GIE default-plugins, see: https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/main/config/charts/inferencepool/templates/epp-config.yaml#L7C3-L56C33
  pluginsCustomConfig:
    ${LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS}: |
$(cat $LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS_FULL_PATH | $LLMDBENCH_CONTROL_SCMD -e 's|^|      |')
inferencePool:
  targetPortNumber: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
  modelServerType: vllm
  modelServers:
    matchLabels:
      llm-d.ai/inferenceServing: "true"
      # llm-d.ai/model: ms-simple-llm-d-modelservice
EOF

    announce "🚀 Installing helm chart \"gaie-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}\" via helmfile..."
    llmdbench_execute_cmd "helmfile --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --kubeconfig ${LLMDBENCH_CONTROL_WORK_DIR}/environment/context.ctx --selector name=gaie-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/helmfile.yaml --skip-diff-on-install" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
    announce "✅ gaie-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE} helm chart deployed successfully"

    srl=deployment,service,route,pods,secrets
    announce "ℹ️ A snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_VLLM_COMMON_NAMESPACE}\":"
    if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 0 ]]; then
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} get --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} $srl" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
    fi
  done
else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
