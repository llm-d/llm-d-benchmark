#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

announce "🔍 Checking if \"${LLMDBENCH_VLLM_COMMON_NAMESPACE}\" is prepared."

llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} delete job download-model --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
  modelfn=$(echo ${model} | ${LLMDBENCH_CONTROL_SCMD} 's^/^___^g' )
  cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_prepare_namespace_${modelfn}.yaml
sampleApplication:
  enabled: true
  baseConfigMapRefName: basic-gpu-with-nixl-and-redis-lookup-preset
  model:
    modelArtifactURI: pvc://$LLMDBENCH_VLLM_COMMON_PVC_NAME/models/$(model_attribute $model model)
    modelName: "$(model_attribute $model model)"
EOF

  llmd_opts="--namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --skip-infra --download-only --storage-class ${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS} --storage-size ${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE} --download-model $(model_attribute $model model) --download-timeout ${LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT} --values-file $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_prepare_namespace_${modelfn}.yaml"
  announce "🚀 Calling llm-d-deployer with options \"${llmd_opts}\"..."
  pushd $LLMDBENCH_DEPLOYER_DIR/llm-d-deployer/quickstart &>/dev/null
  llmdbench_execute_cmd "cd $LLMDBENCH_DEPLOYER_DIR/llm-d-deployer/quickstart; export KUBECONFIG=$LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx; export HF_TOKEN=$LLMDBENCH_HF_TOKEN; export PREPARE_ONLY=true; ./llmd-installer.sh $llmd_opts" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
  popd &>/dev/null
  announce "✅ llm-d-deployer prepared namespace"
done

if [[ $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -eq 1 ]]; then
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z ${LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT} \
-n $LLMDBENCH_VLLM_COMMON_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
privileged \
-z ${LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT} \
-n $LLMDBENCH_VLLM_COMMON_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
fi
announce "✅ Namespace \"${LLMDBENCH_VLLM_COMMON_NAMESPACE}\" prepared."
