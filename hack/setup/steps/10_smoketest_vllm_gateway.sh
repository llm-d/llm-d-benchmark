#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

is_env_type=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep vllm || true)
if [[ ! -z ${is_env_type} ]]
then
  echo "Running smoketest for inference-gateway..."
  inference_gateway_list=$(${LLMDBENCH_KCMD} get service -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --no-headers -o name | grep inference-gateway || true)
  if [[ ! -z ${inference_gateway_list} ]]; then
    for service in ${inference_gateway_list}; do
    clusterip=$(${LLMDBENCH_KCMD} get $service -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --no-headers -o "custom-columns=CLUSTER-IP:spec.clusterIP" || true)
    if [[ -z ${clusterip} ]]
    then
      echo "❌ Could not find an address for inference-gateway. Unable to proceed."
      exit 1
    else
      llmdbench_execute_cmd "${LLMDBENCH_KCMD} run testinference-gateway -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --attach --restart=Never --rm --image=ubi9/ubi --quiet --command -- bash -c \"curl --no-progress-meter http://${clusterip}:80/v1/models\" | jq ." ${LLMDBENCH_DRY_RUN} 1
      echo "✅ Model \"$model\" seems to be up and running."
    fi
    done
  fi
else
  echo "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi