#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

announce "🔍 Checking if current deployment was successfull..."
if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
  pod_string=standalone
  service_ip=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_CLUSTER_NAMESPACE" get service --no-headers | grep ${pod_string} | awk '{print $3}' || true)
else
  pod_string=decode
  service_ip=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_CLUSTER_NAMESPACE" get gateway --no-headers | tail -n1 | awk '{print $3}')
fi

for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
  if [[ $LLMDBENCH_CONTROL_DRY_RUN -ne 0 ]]; then
    pod_ip_list="127.0.0.4"
    service_ip="127.0.0.8"
  else
    pod_ip_list=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_CLUSTER_NAMESPACE" get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' | grep ${pod_string} | awk '{print $2}')
  fi

  if [[ -z $pod_ip_list ]]; then
    announce "❌ Unable to find IPs for pods \"${pod_string}\"!"
    exit 1
  fi

  announce "🚀 Testing all pods \"${pod_string}\"..."
  for pod_ip in $pod_ip_list; do
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} run testinference-pod -n ${LLMDBENCH_CLUSTER_NAMESPACE} --attach --restart=Never --rm --image=ubi9/ubi --quiet --command -- bash -c \"curl --no-progress-meter http://${pod_ip}:${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}/v1/models\" | jq ." ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 2
  done
  announce "✅ All pods respond successfully"

  if [[ -z $service_ip ]]; then
    announce "❌ Unable to find IP for service/gateway \"${pod_string}\"!"
    exit 1
  fi

  announce "🚀 Testing service/gateway \"${service_ip}\"..."
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} run testinference-gateway -n ${LLMDBENCH_CLUSTER_NAMESPACE} --attach --restart=Never --rm --image=ubi9/ubi --quiet --command -- bash -c \"curl --no-progress-meter http://${service_ip}:80/v1/models\" | jq ." ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 2
  announce "✅ Service responds successfully"

  route_url=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_CLUSTER_NAMESPACE" get route --no-headers --ignore-not-found | grep ${pod_string} | awk '{print $2}'  || true)
  if [[ ! -z $route_url ]]; then
    announce "🚀 Testing external route \"${route_url}\"..."
    llmdbench_execute_cmd "curl --no-progress-meter http://${route_url}:80/v1/models | jq ." ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 2
    announce "✅ External route responds successfully"
  fi
done