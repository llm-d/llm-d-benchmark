#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

if [[ $LLMDBENCH_IS_OPENSHIFT -eq 1 ]]
then
  llmdbench_execute_cmd "${LLMDBENCH_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} \
-n $LLMDBENCH_OPENSHIFT_NAMESPACE" ${LLMDBENCH_DRY_RUN}

  llmdbench_execute_cmd "${LLMDBENCH_KCMD} \
adm \
policy \
add-scc-to-user \
privileged \
-z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} \
-n $LLMDBENCH_OPENSHIFT_NAMESPACE" ${LLMDBENCH_DRY_RUN}
fi

is_env_type=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep standalone || true)
if [[ ! -z ${is_env_type} ]]
then
  echo "Preparing OpenShift namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE}..."

  cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: standalone-hf-token
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
type: Opaque
stringData:
  token: ${LLMDBENCH_HF_TOKEN}
EOF

  llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_secret.yaml" ${LLMDBENCH_DRY_RUN}

  is_qs=$(${LLMDBENCH_KCMD} -n $LLMDBENCH_OPENSHIFT_NAMESPACE get secrets/standalone-quay-secret -o name --ignore-not-found=true | cut -d '/' -f 2)
  if [[ -z $is_qs ]]; then
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} create secret docker-registry standalone-quay-secret \
  --docker-server=quay.io \
  --docker-username="${LLMDBENCH_QUAY_USER}" \
  --docker-password="${LLMDBENCH_QUAY_PASSWORD}" \
  --docker-email="${LLMDBENCH_DOCKER_EMAIL}" \
  -n ${LLMDBENCH_OPENSHIFT_NAMESPACE}" ${LLMDBENCH_DRY_RUN}
  fi

  llmdbench_execute_cmd "${LLMDBENCH_KCMD} patch serviceaccount ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} \
  -n ${LLMDBENCH_OPENSHIFT_NAMESPACE} \
  --type=merge \
  -p '{\"imagePullSecrets\":[{\"name\":\"standalone-quay-secret\"}]}'" ${LLMDBENCH_DRY_RUN}
else
  echo "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi