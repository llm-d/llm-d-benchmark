#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

announce "🔍 Checking if namespace '${LLMDBENCH_CLUSTER_NAMESPACE}' exists..."

if ! ${LLMDBENCH_CONTROL_KCMD} get namespace "$LLMDBENCH_CLUSTER_NAMESPACE" --ignore-not-found | grep -q "$LLMDBENCH_CLUSTER_NAMESPACE"; then
  if [[ $LLMDBENCH_USER_IS_ADMIN -eq 1 ]]; then
#  cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_ns_and_sa_and_rbac.yaml
  cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_ns.yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: ${LLMDBENCH_CLUSTER_NAMESPACE}
  labels:
    kubernetes.io/metadata.name: ${LLMDBENCH_CLUSTER_NAMESPACE}
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
#---
#apiVersion: v1
#kind: ServiceAccount
#metadata:
#  name: ${LLMDBENCH_CLUSTER_NAMESPACE}
#  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
#---
#apiVersion: rbac.authorization.k8s.io/v1
#kind: ClusterRoleBinding
#metadata:
#  name: ${LLMDBENCH_CLUSTER_NAMESPACE}
#roleRef:
#  apiGroup: rbac.authorization.k8s.io
#  kind: ClusterRole
#  name: system:openshift:scc:privileged
#subjects:
#  - kind: ServiceAccount
#    name: ${LLMDBENCH_CLUSTER_NAMESPACE}
#    namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
#---
EOF
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/00_ns.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  else
    announce "⚠️ Namespace '${LLMDBENCH_CLUSTER_NAMESPACE}' not found. Stopping..."
    exit 1
  fi
else
  announce "✅ Namespace '${LLMDBENCH_CLUSTER_NAMESPACE}' exists."
fi