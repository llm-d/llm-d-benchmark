#!/usr/bin/env bash

source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
  extract_environment
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "Running smoketest for ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE} model ${model}..."
    clusterip=$(${LLMDBENCH_CONTROL_KCMD} get service -n ${LLMDBENCH_CLUSTER_NAMESPACE} --no-headers -o "custom-columns=NAME:.metadata.name,CLUSTER-IP:spec.clusterIP" | grep ${LLMDBENCH_MODEL2PARAM[${model}:label]} || true)
    clusterip=$(echo ${clusterip} | awk '{print $2}')
    if [[ -z ${clusterip} ]]
    then
      announce "❌ Could not find an address for model \"{$model}\". Unable to proceed."
      exit 1
    else
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} run test${model} -n ${LLMDBENCH_CLUSTER_NAMESPACE} --attach --restart=Never --rm --image=ubi9/ubi --quiet --command -- bash -c \"curl --no-progress-meter http://${clusterip}:80/v1/models\" | jq ." ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 2
      announce "✅ Model \"$model\" seems to be up and running."
    fi
  done
else
  announce "ℹ️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi