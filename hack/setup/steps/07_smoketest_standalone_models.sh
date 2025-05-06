#!/usr/bin/env bash

source ${LLMDBENCH_STEPS_DIR}/env.sh

is_env_type=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep standalone || true)
if [[ ! -z ${is_env_type} ]]
then
  echo "Running smoketest for all deployed models..."
  for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
    clusterip=$(${LLMDBENCH_KCMD} get service -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --no-headers -o "custom-columns=NAME:.metadata.name,CLUSTER-IP:spec.clusterIP" | grep ${LLMDBENCH_MODEL2PARAM[${model}:label]} || true)
    clusterip=$(echo ${clusterip} | awk '{print $2}')
    if [[ -z ${clusterip} ]]
    then
      echo "❌ Could not find an address for model \"{$model}\". Unable to proceed."
      exit 1
    else
      llmdbench_execute_cmd "${LLMDBENCH_KCMD} run test${model} -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --attach --restart=Never --rm --image=ubi9/ubi --quiet --command -- bash -c \"curl --no-progress-meter http://${clusterip}:80/v1/models\" | jq ." ${LLMDBENCH_DRY_RUN} 1
      echo "✅ Model \"$model\" seems to be up and running."
    fi
  done
else
  echo "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi