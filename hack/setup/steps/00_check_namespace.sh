#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "🔍 Checking if namespace '${LLMDBENCH_OPENSHIFT_NAMESPACE}' exists..."

if ! ${LLMDBENCH_KCMD} get namespace "$LLMDBENCH_OPENSHIFT_NAMESPACE" --ignore-not-found | grep -q "$LLMDBENCH_OPENSHIFT_NAMESPACE"; then
  if [[ $(${LLMDBENCH_KCMD} whoami) == "system:admin" ]]; then
  cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_ns_and_sa_and_rbac.yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
  labels:
    kubernetes.io/metadata.name: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
    pod-security.kubernetes.io/audit: privileged
    pod-security.kubernetes.io/enforce: privileged
    pod-security.kubernetes.io/warn: privileged
    security.openshift.io/scc.podSecurityLabelSync: "false"
  annotations:
    openshift.io/sa.scc.mcs: s0:c29,c19
    openshift.io/sa.scc.supplemental-groups: 1000850000/10000
    openshift.io/sa.scc.uid-range: 1000850000/10000
spec:
  finalizers:
  - kubernetes
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: system:openshift:scc:privileged
subjects:
  - kind: ServiceAccount
    name: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
    namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
---
EOF
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_TEMPDIR/00_ns_and_sa_and_rbac.yaml" ${LLMDBENCH_DRY_RUN}
  else
    echo "⚠️  Namespace '${LLMDBENCH_OPENSHIFT_NAMESPACE}' not found. Stopping..."
    exit 1
  fi
else
  echo "✅ Namespace '${LLMDBENCH_OPENSHIFT_NAMESPACE}' exists."
fi