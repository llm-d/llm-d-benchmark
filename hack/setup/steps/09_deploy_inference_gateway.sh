#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

is_env_type=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep vllm || true)
if [[ ! -z ${is_env_type} ]]
then
  echo "Deploying Inference Gateway..."

  VERSION="v0.3.0"
  if [[ $LLMDBENCH_USER_IS_ADMIN -eq 1 ]]
  then
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${VERSION}/manifests.yaml" ${LLMDBENCH_DRY_RUN}
  fi

#  pushd ${LLMDBENCH_GAIE_DIR} &>/dev/null
#  if [[ ! -d gateway-api-inference-extension ]]; then
#      git clone https://github.com/neuralmagic/gateway-api-inference-extension.git
#  fi
#  pushd gateway-api-inference-extension &>/dev/null

  for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
    echo "Creating CRDs required for inference gateway for model \"${model}\" (from files located at $LLMDBENCH_TEMPDIR)..."

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_a_${model}_gateway_parameters.yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: inference-gateway-params
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  kube:
    service:
      type: ClusterIP
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_b_${model}_gateway.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: inference-gateway
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  gatewayClassName: kgateway
  infrastructure:
    parametersRef:
      group: gateway.kgateway.dev
      kind: GatewayParameters
      name: inference-gateway-params
  listeners:
  - name: http
    port: 80
    protocol: HTTP
    allowedRoutes:
      namespaces:
        from: Same
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_c_${model}_httproute.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-route
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  parentRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: inference-gateway
  rules:
  - backendRefs:
    - group: inference.networking.x-k8s.io
      kind: InferencePool
      name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}
      port: 8000
    matches:
    - path:
        type: PathPrefix
        value: /
    timeouts:
      request: 300s
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_d_${model}_inferencepool.yaml
apiVersion: inference.networking.x-k8s.io/v1alpha2
kind: InferencePool
metadata:
  name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  targetPortNumber: 8000
  selector:
    app: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  extensionRef:
    name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-epp
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_e_${model}_service.yaml
apiVersion: v1
kind: Service
metadata:
  name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-epp
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  selector:
    app: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-epp
  ports:
    - protocol: TCP
      port: 9002
      targetPort: 9002
      appProtocol: http2
  type: ClusterIP
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_f_${model}_deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-epp
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
  labels:
    app: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-epp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-epp
  template:
    metadata:
      labels:
        app: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-epp
    spec:
      # Conservatively, this timeout should mirror the longest grace period of the pods within the pool
      terminationGracePeriodSeconds: 130
      containers:
      - name: epp
        image: ${LLMDBENCH_EPP_IMAGE}
        imagePullPolicy: Always
        args:
        - -poolName
        - "vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}"
        - "-poolNamespace"
        - "${LLMDBENCH_OPENSHIFT_NAMESPACE}"
        - -v
        - "4"
        - --zap-encoder
        - "json"
        - -grpcPort
        - "9002"
        - -grpcHealthPort
        - "9003"
        ports:
        - containerPort: 9002
        - containerPort: 9003
        - name: metrics
          containerPort: 9090
        livenessProbe:
          grpc:
            port: 9003
            service: inference-extension
          initialDelaySeconds: 5
          periodSeconds: 10
        readinessProbe:
          grpc:
            port: 9003
            service: inference-extension
          initialDelaySeconds: 5
          periodSeconds: 10
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_g_${model}_role.yaml
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: pod-read
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
rules:
- apiGroups: ["inference.networking.x-k8s.io"]
  resources: ["inferencemodels"]
  verbs: ["get", "watch", "list"]
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "watch", "list"]
- apiGroups: ["inference.networking.x-k8s.io"]
  resources: ["inferencepools"]
  verbs: ["get", "watch", "list"]
- apiGroups: ["discovery.k8s.io"]
  resources: ["endpointslices"]
  verbs: ["get", "watch", "list"]
- apiGroups:
  - authentication.k8s.io
  resources:
  - tokenreviews
  verbs:
  - create
- apiGroups:
  - authorization.k8s.io
  resources:
  - subjectaccessreviews
  verbs:
  - create
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_h_${model}_rolebinding.yaml
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: pod-read-binding
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
subjects:
- kind: ServiceAccount
  name: ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
roleRef:
  kind: Role
  name: pod-read
EOF

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_i_${model}_inferencepool.yaml
apiVersion: inference.networking.x-k8s.io/v1alpha2
kind: InferenceModel
metadata:
  name: base-model
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  modelName: ${LLMDBENCH_MODEL2PARAM[${model}:name]}
  criticality: Critical
  poolRef:
    name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}
EOF

    for rf in $(ls $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_*_${model}*); do
      llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $rf" ${LLMDBENCH_DRY_RUN}
    done
  done
else
  echo "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi

oc expose service inference-gateway --name=llm-route

echo -e "\nA snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_OPENSHIFT_NAMESPACE}\":\n"
${LLMDBENCH_KCMD} get --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} gatewayparameters,gateway,httproute,service,deployment,pods,secrets

#popd &>/dev/null
