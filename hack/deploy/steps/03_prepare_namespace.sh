#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -eq 1 ]]
then
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z ${LLMDBENCH_CLUSTER_SERVICE_ACCOUNT} \
-n $LLMDBENCH_CLUSTER_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
privileged \
-z ${LLMDBENCH_CLUSTER_SERVICE_ACCOUNT} \
-n $LLMDBENCH_CLUSTER_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
fi

is_env_type=$(echo $LLMDBENCH_DEPLOY_METHODS | grep standalone || true)
if [[ ! -z ${is_env_type} ]]
then
  announce "Preparing OpenShift namespace ${LLMDBENCH_CLUSTER_NAMESPACE}..."

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    should_create=0
    is_secret=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} get secret ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME} --ignore-not-found=true)
    if [[ -z ${is_secret} ]]; then
      should_create=1
    fi

    is_key=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} get secret ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME} -o json | jq -r .data | grep token_${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]} || true)
    if [[ -z $is_key ]]; then
      should_create=1
    fi

    if [[ ${should_create} -eq 1 ]]; then
      required_vars=("LLMDBENCH_HF_TOKEN")
      for var in "${required_vars[@]}"; do
        if [ -z "${!var:-}" ]; then
          echo "❌ Environment variable '$var' is not set."
          exit 1
        fi
      done

      cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
type: Opaque
stringData:
  token_${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}: ${LLMDBENCH_HF_TOKEN}
EOF

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
  done

  is_qs=$(${LLMDBENCH_CONTROL_KCMD} -n $LLMDBENCH_CLUSTER_NAMESPACE get secrets/${LLMDBENCH_VLLM_COMMON_PULL_SECRET_NAME} -o name --ignore-not-found=true | cut -d '/' -f 2)
  if [[ -z $is_qs ]]; then
      required_vars=("LLMDBENCH_QUAY_USER" "LLMDBENCH_QUAY_PASSWORD")
      for var in "${required_vars[@]}"; do
        if [ -z "${!var:-}" ]; then
          echo "❌ Environment variable '$var' is not set."
          exit 1
        fi
      done

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} create secret docker-registry ${LLMDBENCH_VLLM_COMMON_PULL_SECRET_NAME} \
  --docker-server=quay.io \
  --docker-username="${LLMDBENCH_QUAY_USER}" \
  --docker-password="${LLMDBENCH_QUAY_PASSWORD}" \
  --docker-email="${LLMDBENCH_DOCKER_EMAIL}" \
  -n ${LLMDBENCH_CLUSTER_NAMESPACE}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  fi

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} patch serviceaccount ${LLMDBENCH_CLUSTER_SERVICE_ACCOUNT} \
  -n ${LLMDBENCH_CLUSTER_NAMESPACE} \
  --type=merge \
  -p '{\"imagePullSecrets\":[{\"name\":\"${LLMDBENCH_VLLM_COMMON_PULL_SECRET_NAME}\"}]}'" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
else
  announce "ℹ️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi