#!/usr/bin/env bash
source ${LLMDBENCH_DIR}/env.sh

if [[ $LLMDBENCH_ENVIRONMENT_TYPE_P2P_ACTIVE -eq 1 ]]; then
  announce "Deploying Inference Gateway..."

  VERSION="v0.3.0"
  if [[ $LLMDBENCH_USER_IS_ADMIN -eq 1 ]]; then
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${VERSION}/manifests.yaml" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}
  fi

#  pushd ${LLMDBENCH_GAIE_DIR} &>/dev/null
#  if [[ ! -d gateway-api-inference-extension ]]; then
#      git clone https://github.com/neuralmagic/gateway-api-inference-extension.git
#  fi
#  pushd gateway-api-inference-extension &>/dev/null

  for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
    announce "Creating CRDs required for inference gateway for model \"${model}\" (from files located at $LLMDBENCH_WORK_DIR)..."

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_a_${model}_service_account.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: endpoint-picker
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_b_${model}_role.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: endpoint-picker
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
rules:
- apiGroups:
  - inference.networking.x-k8s.io
  resources:
  - inferencepools
  - inferencemodels
  verbs:
  - get
  - watch
  - list
- apiGroups:
  - ""
  resources:
  - pods
  verbs:
  - get
  - watch
  - list
- apiGroups:
  - discovery.k8s.io
  resources:
  - endpointslices
  verbs:
  - get
  - watch
  - list
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

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_c_${model}_rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: endpoint-picker-binding
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: endpoint-picker
subjects:
- kind: ServiceAccount
  name: endpoint-picker
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_d_${model}_secret.yaml
apiVersion: v1
data:
  inference-gateway-secret-key: $(echo -n ${LLMDBENCH_HF_TOKEN} | base64 | tr -d '\n')
kind: Secret
metadata:
  labels:
    app.kubernetes.io/component: secret
    app.kubernetes.io/name: vllm
  name: inference-gateway-secret
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
type: Opaque
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_e_${model}_service.yaml
apiVersion: v1
kind: Service
metadata:
  name: endpoint-picker
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  ports:
  - appProtocol: http2
    port: 9002
    protocol: TCP
    targetPort: 9002
  selector:
    app: endpoint-picker
  type: ClusterIP
EOF

  is_qs=$(${LLMDBENCH_KCMD} -n $LLMDBENCH_OPENSHIFT_NAMESPACE get secrets/inference-gateway-quay-secret -o name --ignore-not-found=true | cut -d '/' -f 2)
  if [[ -z $is_qs ]]; then
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} create secret docker-registry inference-gateway-quay-secret \
  --docker-server=quay.io \
  --docker-username="${LLMDBENCH_QUAY_USER}" \
  --docker-password="${LLMDBENCH_QUAY_PASSWORD}" \
  --docker-email="${LLMDBENCH_DOCKER_EMAIL}" \
  -n ${LLMDBENCH_OPENSHIFT_NAMESPACE}" ${LLMDBENCH_DRY_RUN}
  fi

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_f_${model}_deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    app: endpoint-picker
  name: endpoint-picker
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: endpoint-picker
  template:
    metadata:
      labels:
        app: endpoint-picker
    spec:
      containers:
      - args:
        - -poolName
        - "vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct"
        - -poolNamespace
        - "${LLMDBENCH_OPENSHIFT_NAMESPACE}"
        - -v
        - "4"
        - --zap-encoder
        - json
        - -grpcPort
        - "9002"
        - -grpcHealthPort
        - "9003"
        env:
        - name: KVCACHE_INDEXER_REDIS_ADDR
          value: vllm-p2p-${model}.${LLMDBENCH_OPENSHIFT_NAMESPACE}.svc.cluster.local:${LLMDBENCH_REDIS_PORT}
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              key: inference-gateway-secret-key
              name: inference-gateway-secret
        - name: ENABLE_KVCACHE_AWARE_SCORER
          value: "${LLMDBENCH_EPP_ENABLE_KVCACHE_AWARE_SCORER}"
        - name: KVCACHE_AWARE_SCORER_WEIGHT
          value: "${LLMDBENCH_EPP_KVCACHE_AWARE_SCORER_WEIGHT}"
        - name: ENABLE_LOAD_AWARE_SCORER
          value: "${LLMDBENCH_EPP_ENABLE_LOAD_AWARE_SCORER}"
        - name: LOAD_AWARE_SCORER_WEIGHT
          value: "${LLMDBENCH_EPP_LOAD_AWARE_SCORER_WEIGHT}"
        - name: ENABLE_PREFIX_AWARE_SCORER
          value: "${LLMDBENCH_EPP_ENABLE_PREFIX_AWARE_SCORER}"
        - name: PREFIX_AWARE_SCORER_WEIGHT
          value: "${LLMDBENCH_EPP_PREFIX_AWARE_SCORER_WEIGHT}"
        - name: PD_ENABLED
          value: "${LLMDBENCH_EPP_PREFIX_AWARE_SCORER_WEIGHT}"
        image: ${LLMDBENCH_EPP_IMAGE}
        imagePullPolicy: IfNotPresent
        livenessProbe:
          grpc:
            port: 9003
            service: inference-extension
          initialDelaySeconds: 5
          periodSeconds: 10
        name: epp
        ports:
        - containerPort: 9002
        - containerPort: 9003
        - containerPort: 9090
          name: metrics
        readinessProbe:
          grpc:
            port: 9003
            service: inference-extension
          initialDelaySeconds: 5
          periodSeconds: 10
      imagePullSecrets:
      - name: inference-gateway-quay-secret
      serviceAccountName: endpoint-picker
      terminationGracePeriodSeconds: 130
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_g_${model}_gateway_parameters.yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: inference-gateway-params
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  kube:
    envoyContainer:
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        runAsNonRoot: true
        runAsUser: ${LLMDBENCH_PROXY_UID}
    podTemplate:
      extraLabels:
        gateway: custom
      securityContext:
        seccompProfile:
          type: RuntimeDefault
    service:
      extraLabels:
        gateway: custom
      type: ClusterIP
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_h_${model}_gateway.yaml
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
  - name: default
    port: 80
    protocol: HTTP
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_i_${model}_httproute.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: inference-route
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  parentRefs:
  - name: inference-gateway
  rules:
  - backendRefs:
    - group: inference.networking.x-k8s.io
      kind: InferencePool
      name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct
      port: 8000
    matches:
    - path:
        type: PathPrefix
        value: /
    timeouts:
      request: 30s
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_j_${model}_inferencepool.yaml
apiVersion: inference.networking.x-k8s.io/v1alpha2
kind: InferencePool
metadata:
  name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  extensionRef:
    name: endpoint-picker
  selector:
    app: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  targetPortNumber: 8000
EOF

    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_k_${model}_inferencemodel.yaml
apiVersion: inference.networking.x-k8s.io/v1alpha2
kind: InferenceModel
metadata:
  name: base-model
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  modelName: ${LLMDBENCH_MODEL2PARAM[${model}:name]}
  criticality: Critical
  modelName: ${LLMDBENCH_MODEL2PARAM[${model}:name]}
  poolRef:
    name: vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct
EOF

    for rf in $(ls $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_*_${model}*); do
      llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $rf" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}
    done
  done
else
  announce "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi

for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
  announce "ℹ️  Waiting for ${model} to be Ready (timeout=${LLMDBENCH_WAIT_TIMEOUT}s)..."
  llmdbench_execute_cmd "${LLMDBENCH_KCMD} --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} wait --timeout=${LLMDBENCH_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l app=endpoint-picker" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}

  is_route=$(${LLMDBENCH_KCMD} --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} get route --ignore-not-found | grep llm-route || true)
  if [[ -z $is_route ]]
  then
    llmdbench_execute_cmd "oc expose service inference-gateway --name=llm-route" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}
  fi
    announce "ℹ️  endpoint picker ${model} to be Ready (timeout=${LLMDBENCH_WAIT_TIMEOUT}s)..."
done

announce "A snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_OPENSHIFT_NAMESPACE}\":"
${LLMDBENCH_KCMD} get --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} gatewayparameters,gateway,httproute,service,deployment,pods,secrets

#popd &>/dev/null
