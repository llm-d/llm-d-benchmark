#!/usr/bin/env bash
source ${LLMDBENCH_DIR}/env.sh

if [[ $LLMDBENCH_ENVIRONMENT_TYPE_P2P_ACTIVE -eq 1 ]]; then
  announce "Running smoketest for inference-gateway..."
  inference_gateway_list=$(${LLMDBENCH_KCMD} get service -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --no-headers -o name | grep inference-gateway || true)
  if [[ ! -z ${inference_gateway_list} ]]; then
    for service in ${inference_gateway_list}; do
    clusterip=$(${LLMDBENCH_KCMD} get $service -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --no-headers -o "custom-columns=CLUSTER-IP:spec.clusterIP" || true)
    if [[ -z ${clusterip} ]]
    then
      announce "❌ Could not find an address for inference-gateway. Unable to proceed."
      exit 1
    else
      llmdbench_execute_cmd "${LLMDBENCH_KCMD} run testinference-gateway -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} --attach --restart=Never --rm --image=ubi9/ubi --quiet --command -- bash -c \"curl --no-progress-meter http://${clusterip}:80/v1/models\" | jq ." ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}
      announce "✅ Inference gateway seems to be up and running."
    fi
    done
  fi
else
  announce "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi