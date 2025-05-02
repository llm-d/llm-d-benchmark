#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Preparing OpenShift namespace..."
oc login --token="${OPENSHIFT_TOKEN}" --server="${OPENSHIFT_HOST}:6443" || true

oc adm policy add-scc-to-user anyuid -z default -n "$OPENSHIFT_NAMESPACE"
oc adm policy add-scc-to-user privileged -z default -n "$OPENSHIFT_NAMESPACE"

oc apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: hf-token
  namespace: ${OPENSHIFT_NAMESPACE}
type: Opaque
stringData:
  token: ${HF_TOKEN}
EOF

oc create secret docker-registry quay-secret \
  --docker-server=quay.io \
  --docker-username="${QUAY_USER}" \
  --docker-password="${QUAY_PASSWORD}" \
  --docker-email="${DOCKER_EMAIL}" \
  -n "${OPENSHIFT_NAMESPACE}" || true

oc patch serviceaccount default \
  -n "${OPENSHIFT_NAMESPACE}" \
  --type=merge \
  -p '{"imagePullSecrets":[{"name":"quay-secret"}]}'
