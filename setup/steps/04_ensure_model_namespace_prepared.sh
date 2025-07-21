#!/usr/bin/env bash
source "${LLMDBENCH_CONTROL_DIR}/env.sh"

main() {
  announce "🔍 Checking if \"${LLMDBENCH_VLLM_COMMON_NAMESPACE}\" is prepared."
  check_storage_class_and_affinity
  if [[ $? -ne 0 ]]
  then
    announce "❌ Failed to check storage class and affinity"
    exit 1
  fi

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace "${LLMDBENCH_VLLM_COMMON_NAMESPACE}" delete job download-model --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    local MODEL_ARTIFACT_URI="pvc://${LLMDBENCH_VLLM_COMMON_PVC_NAME}/models/$(model_attribute "${model}" model)"
    local PROTOCOL="${MODEL_ARTIFACT_URI%%://*}"
    local DOWNLOAD_MODEL="$(model_attribute "${model}" model)"

    local PVC_AND_MODEL_PATH="${MODEL_ARTIFACT_URI#*://}"
    local PVC_NAME="${PVC_AND_MODEL_PATH%%/*}"
    local MODEL_PATH="${PVC_AND_MODEL_PATH#*/}"

    create_namespace "${LLMDBENCH_CONTROL_KCMD}" "${LLMDBENCH_VLLM_COMMON_NAMESPACE}"
    create_or_update_hf_secret "${LLMDBENCH_CONTROL_KCMD}" "${LLMDBENCH_VLLM_COMMON_NAMESPACE}" "${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}" ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_KEY} "${LLMDBENCH_HF_TOKEN}"

    validate_and_create_pvc \
      "${LLMDBENCH_CONTROL_KCMD}" \
      "${LLMDBENCH_VLLM_COMMON_NAMESPACE}" \
      "${DOWNLOAD_MODEL}" \
      "${PVC_NAME}" \
      "${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE}" \
      "${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS}"

    launch_download_job \
      "${LLMDBENCH_CONTROL_KCMD}" \
      "${LLMDBENCH_VLLM_COMMON_NAMESPACE}" \
      "${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}" \
      "${DOWNLOAD_MODEL}" \
      "${MODEL_PATH}" \
      "${PVC_NAME}"

    wait_for_download_job \
      "${LLMDBENCH_CONTROL_KCMD}" \
      "${LLMDBENCH_VLLM_COMMON_NAMESPACE}" \
      "${LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT}"

    announce "✅ llm-d-deployer prepared namespace"

    if [[ "${LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT}" -eq 1 ]]; then
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
        adm policy add-scc-to-user anyuid \
        -z ${LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT} \
        -n ${LLMDBENCH_VLLM_COMMON_NAMESPACE}" \
        "${LLMDBENCH_CONTROL_DRY_RUN}" "${LLMDBENCH_CONTROL_VERBOSE}" 1 1 1

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
        adm policy add-scc-to-user privileged \
        -z ${LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT} \
        -n ${LLMDBENCH_VLLM_COMMON_NAMESPACE}" \
        "${LLMDBENCH_CONTROL_DRY_RUN}" "${LLMDBENCH_CONTROL_VERBOSE}" 1 1 1
    fi

    announce "✅ Namespace \"${LLMDBENCH_VLLM_COMMON_NAMESPACE}\" prepared."
  done

  return 0
}

main
