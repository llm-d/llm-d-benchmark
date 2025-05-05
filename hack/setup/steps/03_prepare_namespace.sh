#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "Preparing OpenShift namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE}..."

${LLMDBENCH_KCMD} adm policy add-scc-to-user anyuid -z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} -n "$LLMDBENCH_OPENSHIFT_NAMESPACE"
${LLMDBENCH_KCMD} adm policy add-scc-to-user privileged -z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} -n "$LLMDBENCH_OPENSHIFT_NAMESPACE"

${LLMDBENCH_KCMD} apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: hf-token
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
type: Opaque
stringData:
  token: ${LLMDBENCH_HF_TOKEN}
EOF

${LLMDBENCH_KCMD} create secret docker-registry quay-secret \
  --docker-server=quay.io \
  --docker-username="${LLMDBENCH_QUAY_USER}" \
  --docker-password="${LLMDBENCH_QUAY_PASSWORD}" \
  --docker-email="${LLMDBENCH_DOCKER_EMAIL}" \
  -n "${LLMDBENCH_OPENSHIFT_NAMESPACE}" || true

${LLMDBENCH_KCMD} patch serviceaccount default \
  -n "${LLMDBENCH_OPENSHIFT_NAMESPACE}" \
  --type=merge \
  -p '{"imagePullSecrets":[{"name":"quay-secret"}]}'
