#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

announce "🔍 Checking if current deployment was successful..."
if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
  pod_string=standalone
  route_string=standalone
  service=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get service --no-headers | grep ${pod_string})
  service_type=service
else
  pod_string=decode
  route_string=${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}-inference-gateway
  service=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get gateway --no-headers | grep ^infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}-inference-gateway)
  service_type=gateway
fi

if [[ $LLMDBENCH_CONTROL_DRY_RUN -ne 0 ]]; then
  service_name=localhost
  service_ip="127.0.0.8"
else
  if [[ $(echo $service | wc -w) -eq 0 ]]; then
    announce "❌ No $service_type found with string \"${pod_string}\"!"
    exit 1
  fi
  if [[ $(echo $service | wc -w) -gt 6 ]]; then
    # Each gateway line will have 5 words, while each service line will have 6.
    # If we have more than 6 words, then we know we have more than 1 gateway/service.
    announce "❌ Cannot uniquely identify $service_type with string \"${pod_string}\"!"
    exit 1
  fi

  service_name=$(echo "${service}" | awk '{print $1}')
  service_ip=$(echo "${service}" | awk '{print $3}')
fi

service_name="${service_name}.${LLMDBENCH_VLLM_COMMON_NAMESPACE}${LLMDBENCH_VLLM_COMMON_FQDN}"

set +euo pipefail
for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
  export LLMDBENCH_DEPLOY_CURRENT_MODEL=$(model_attribute $model model)
  export LLMDBENCH_DEPLOY_CURRENT_MODELID=$(model_attribute $model modelid)
  export LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL=$(model_attribute $model modelid_label)

  if [[ $LLMDBENCH_CONTROL_DRY_RUN -ne 0 ]]; then
    pod_ip_list="127.0.0.4"
  elif [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
    pod_ip_list=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' | grep ${pod_string} | awk '{print $2}')
  else
    pod_ip_list=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} get pods -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=decode -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' | awk '{print $2}')
  fi

  if [[ -z $pod_ip_list ]]; then
    announce "❌ Unable to find IPs for pods \"${pod_string}\"!"
    exit 1
  fi

  announce "🚀 Testing all pods \"${pod_string}\" (port ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT})..."
  for pod_ip in $pod_ip_list; do
    announce "       🚀 Testing pod ip \"${pod_ip}\" ..."

    if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
      announce "       ✅ Pod ip \"${pod_ip}\" responded successfully ($LLMDBENCH_DEPLOY_CURRENT_MODEL)"
    else

      received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${pod_ip} ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT})

      if [[ $received_model_name == ${LLMDBENCH_DEPLOY_CURRENT_MODEL} ]]; then
        announce "       ✅ Pod ip \"${pod_ip}\" responded successfully ($received_model_name)"
      else
        announce "       ❌ Pod ip \"${pod_ip}\" responded with model name \"$received_model_name\" (instead of $LLMDBENCH_DEPLOY_CURRENT_MODEL)!"
        exit 1
      fi
    fi
  done
  announce "✅ All pods respond successfully"

  if [[ -z $service_ip ]]; then
    announce "❌ Unable to find IP for service/gateway \"${service}\"!"
    exit 1
  fi

  if [[ -z $(not_valid_ip ${service_ip}) ]]; then
    announce "❌ Invalid IP (\"${service_ip}\") for service/gateway \"${service_name}\"!"
    exit 1
  fi

  announce "🚀 Testing service/gateway \"${service_ip}\" (port 80)..."

  if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
    announce "✅ Service responds successfully ($LLMDBENCH_DEPLOY_CURRENT_MODEL)"
  else
    if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
      received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${service_ip} 80)
    else
      received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${service_ip}:80/${LLMDBENCH_DEPLOY_CURRENT_MODELID} 80)
    fi

    if [[ ${received_model_name} == ${LLMDBENCH_DEPLOY_CURRENT_MODEL} ]]; then
      announce "✅ Service responds successfully ($received_model_name)"
    else
      announce "❌ Service responded with model name \"$received_model_name\" (instead of $LLMDBENCH_DEPLOY_CURRENT_MODEL)!"
      exit 1
    fi
  fi

  announce "🚀 Testing service/gateway \"${service_name}\" (port 80)..."

  if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
    announce "✅ Service responds successfully ($LLMDBENCH_DEPLOY_CURRENT_MODEL)"
  else

    received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${service_ip} 80)
    if [[ ${received_model_name} == ${LLMDBENCH_DEPLOY_CURRENT_MODEL} ]]; then
      announce "✅ Service responds successfully ($received_model_name)"
    else
      announce "❌ Service responded with model name \"$received_model_name\" (instead of $LLMDBENCH_DEPLOY_CURRENT_MODEL)!"
      exit 1
    fi
  fi

  if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
    route_url=
  else
    if [[ $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -eq 1 ]]; then
      route_url=$(${LLMDBENCH_CONTROL_KCMD} --namespace "$LLMDBENCH_VLLM_COMMON_NAMESPACE" get route --no-headers --ignore-not-found | grep ${route_string} | awk '{print $2}'  || true)
    fi
  fi

  if [[ ! -z $route_url ]]; then
    announce "🚀 Testing external route \"${route_url}\"..."
    if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
      received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${route_url} 80)
    else
      received_model_name=$(get_model_name_from_pod $LLMDBENCH_VLLM_COMMON_NAMESPACE $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG}) ${route_url}:80/${LLMDBENCH_DEPLOY_CURRENT_MODELID} 80)
    fi
    if [[ ${received_model_name} == ${LLMDBENCH_DEPLOY_CURRENT_MODEL} ]]; then
      announce "✅ External route responds successfully ($received_model_name)"
    else
      announce "❌ External route responded with model name \"$received_model_name\" (instead of $LLMDBENCH_DEPLOY_CURRENT_MODEL)!"
      exit 1
    fi
  fi

done
set -euo pipefail
