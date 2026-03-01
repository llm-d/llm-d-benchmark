#!/usr/bin/env bash

LLMDBENCH_NAMESPACE=testns
LLMDBENCH_SERVICE_ACCOUNT=testsa
if [[ ! -z $KUBECONFIG ]]; then
  LLMDBENCH_SERVER=$(cat $KUBECONFIG  | yq '.clusters[].cluster.server')
else
  LLMDBENCH_SERVER=$(cat ~/.kube/config | yq '.clusters[].cluster.server')
fi
LLMDBENCH_CLOUD=$(echo $LLMDBENCH_SERVER | cut -d '.' -f 2)

#LLMDBENCH_WORK_DIR=$(mktemp -d)
LLMDBENCH_WORK_DIR=/tmp/$(whoami)_$LLMDBENCH_CLOUD
mkdir -p $LLMDBENCH_WORK_DIR

LLMDBENCH_KOP=apply
echo "$0" | grep -q delete
if [[ $? -eq 0 ]]; then
  LLMDBENCH_KOP=delete
fi

cat << EOF > $LLMDBENCH_WORK_DIR/01_namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: $LLMDBENCH_NAMESPACE
EOF

cat << EOF > $LLMDBENCH_WORK_DIR/02_serviceaccount.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $LLMDBENCH_SERVICE_ACCOUNT
  namespace: $LLMDBENCH_NAMESPACE
EOF

cat << EOF > $LLMDBENCH_WORK_DIR/03_clusterrole.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: $LLMDBENCH_SERVICE_ACCOUNT-llm-d-standup-cr
rules:
- nonResourceURLs:
  - "/metrics"
  verbs:
  - "get"
- apiGroups:
  - ""
  resources:
  - persistentvolumeclaims
  - configmaps
  - namespaces
  - secrets
  - jobs
  - pods
  - pods/exec
  - services
  - serviceaccounts
  verbs:
  - create
- apiGroups:
  - "batch"
  resources:
  - jobs
  verbs:
  - get
  - create
  - delete
  - patch
  - watch
  - list
- apiGroups:
  - ""
  resources:
  - nodes
  - pods
  - pods/log
  - jobs
  - clusterroles
  - clusterrolebindings
  - securitycontextconstraints
  - configmaps
  - namespaces
  - secrets
  - persistentvolumeclaims
  - services
  - serviceaccounts
  verbs:
  - get
  - list
  - watch
- apiGroups:
  - ""
  resources:
  - configmaps
  - namespaces
  - secrets
  - jobs
  - pods
  - persistentvolumeclaims
  - services
  verbs:
  - patch
  - update
- apiGroups:
  - ""
  resources:
  - jobs
  - services
  - deployments
  - namespaces
  - secrets
  - pods
  verbs:
  - delete
- apiGroups:
  - "rbac.authorization.k8s.io"
  resources:
  - clusterrolebindings
  - roles
  - rolebindings
  verbs:
  - list
  - get
- apiGroups:
  - "rbac.authorization.k8s.io"
  resources:
  - clusterrolebindings
  - clusterroles
  - roles
  - rolebindings
  verbs:
  - create
- apiGroups:
  - "rbac.authorization.k8s.io"
  resources:
  - clusterrolebindings
  - clusterroles
  - roles
  - rolebindings
  verbs:
  - delete
- apiGroups:
  - "authentication.k8s.io"
  resources:
  - tokenreviews
  verbs:
  - create
- apiGroups:
  - "authorization.k8s.io"
  resources:
  - subjectaccessreviews
  verbs:
  - create
- apiGroups:
  - "apps"
  resources:
  - deployments
  verbs:
  - list
  - get
- apiGroups:
  - "apps"
  resources:
  - deployments
  verbs:
  - create
- apiGroups:
  - "apps"
  resources:
  - deployments
  verbs:
  - delete
- apiGroups:
  - "security.openshift.io"
  resources:
  - securitycontextconstraints
  verbs:
  - patch
  - get
- apiGroups:
  - "networking.k8s.io"
  resources:
  - ingresses
  verbs:
  - get
  - list
- apiGroups:
  - "gateway.networking.k8s.io"
  resources:
  - gateways
  - httproutes
  verbs:
  - get
  - list
- apiGroups:
  - "gateway.networking.k8s.io"
  resources:
  - gateways
  - httproutes
  verbs:
  - create
  - delete
  - patch
- apiGroups:
  - "inference.networking.k8s.io"
  resources:
  - inferencepools
  verbs:
  - get
  - list
  - watch
- apiGroups:
  - "inference.networking.k8s.io"
  resources:
  - inferencepools
  verbs:
  - create
- apiGroups:
  - "inference.networking.k8s.io"
  resources:
  - inferencepools
  verbs:
  - delete
- apiGroups:
  - "inference.networking.x-k8s.io"
  resources:
  - inferencemodels
  - inferenceobjectives
  verbs:
  - get
  - list
  - watch
- apiGroups:
  - "networking.istio.io"
  resources:
  - destinationrules
  verbs:
  - get
- apiGroups:
  - "networking.istio.io"
  resources:
  - destinationrules
  verbs:
  - create
- apiGroups:
  - "networking.istio.io"
  resources:
  - destinationrules
  verbs:
  - delete
- apiGroups:
  - ""
  resources:
  - serviceaccounts
  verbs:
  - impersonate
- apiGroups:
  - ""
  resources:
  - serviceaccounts
  verbs:
  - delete
- apiGroups:
  - "route.openshift.io"
  resources:
  - routes
  verbs:
  - list
- apiGroups:
  - "gateway.kgateway.dev"
  resources:
  - gatewayparameters
  verbs:
  - list
- apiGroups:
  - "route.openshift.io"
  resources:
  - routes
  verbs:
  - delete
- apiGroups:
  - "monitoring.coreos.com"
  resources:
  - servicemonitors
  verbs:
  - get
- apiGroups:
  - "monitoring.coreos.com"
  resources:
  - servicemonitors
  verbs:
  - create
- apiGroups:
  - "monitoring.coreos.com"
  resources:
  - servicemonitors
  verbs:
  - delete
- apiGroups:
  - "apiextensions.k8s.io"
  resources:
  - customresourcedefinitions
  verbs:
  - list
  - get
EOF

cat << EOF > $LLMDBENCH_WORK_DIR/04_clusterrolebinding.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: $LLMDBENCH_SERVICE_ACCOUNT-llm-d-standup-crb
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: $LLMDBENCH_SERVICE_ACCOUNT-llm-d-standup-cr
subjects:
- kind: ServiceAccount
  name: $LLMDBENCH_SERVICE_ACCOUNT
  namespace: $LLMDBENCH_NAMESPACE
EOF

kubectl $LLMDBENCH_KOP -f $LLMDBENCH_WORK_DIR

if [[ $LLMDBENCH_KOP == "apply" ]]; then
  if [[ ! -f $LLMDBENCH_WORK_DIR/token ]]; then
    LLMDBENCH_TOKEN=$(kubectl create token $LLMDBENCH_SERVICE_ACCOUNT -n $LLMDBENCH_NAMESPACE --duration=87600h)
    echo $LLMDBENCH_TOKEN > $LLMDBENCH_WORK_DIR/token
  else
    LLMDBENCH_TOKEN=$(cat $LLMDBENCH_WORK_DIR/token)
  fi
  oc login --insecure-skip-tls-verify=true --token=$LLMDBENCH_TOKEN --server=$LLMDBENCH_SERVER
fi