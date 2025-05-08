#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -eq 1 ]]
then
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} \
-n $LLMDBENCH_OPENSHIFT_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
privileged \
-z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} \
-n $LLMDBENCH_OPENSHIFT_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}
fi

is_env_type=$(echo $LLMDBENCH_DEPLOY_ENVIRONMENT_TYPES | grep standalone || true)
if [[ ! -z ${is_env_type} ]]
then
  announce "Preparing OpenShift namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE}..."

  cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: vllm-common-hf-token
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
type: Opaque
stringData:
  token: ${LLMDBENCH_HF_TOKEN}
EOF

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}

  is_qs=$(${LLMDBENCH_CONTROL_KCMD} -n $LLMDBENCH_OPENSHIFT_NAMESPACE get secrets/vllm-standalone-quay-secret -o name --ignore-not-found=true | cut -d '/' -f 2)
  if [[ -z $is_qs ]]; then
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} create secret docker-registry vllm-standalone-quay-secret \
  --docker-server=quay.io \
  --docker-username="${LLMDBENCH_QUAY_USER}" \
  --docker-password="${LLMDBENCH_QUAY_PASSWORD}" \
  --docker-email="${LLMDBENCH_DOCKER_EMAIL}" \
  -n ${LLMDBENCH_OPENSHIFT_NAMESPACE}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}
  fi

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} patch serviceaccount ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} \
  -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} \
  --type=merge \
  -p '{\"imagePullSecrets\":[{\"name\":\"vllm-standalone-quay-secret\"}]}'" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}
else
  announce "ℹ️ Environment types are \"${LLMDBENCH_DEPLOY_ENVIRONMENT_TYPES}\". Skipping this step."
fi