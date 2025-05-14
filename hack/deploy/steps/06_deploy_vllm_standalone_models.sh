#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then

  extract_environment

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_${model}.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
  labels:
    app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  replicas: ${LLMDBENCH_VLLM_COMMON_REPLICAS}
  selector:
    matchLabels:
      app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
  template:
    metadata:
      labels:
        app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
                operator: In
                values:
                - $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
      containers:
      - name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
        image: ${LLMDBENCH_VLLM_STANDALONE_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          ${LLMDBENCH_MODEL2PARAM[${model}:cmdline]}
        env:
        - name: HF_HOME
          value: ${LLMDBENCH_VLLM_COMMON_PVC_MOUNTPOINT}
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
              key: token_${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
        - name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
          value: "1"
        ports:
        - containerPort: 80
        livenessProbe:
          httpGet: { path: /health, port: 80 }
          initialDelaySeconds: 120
          periodSeconds: 10
        readinessProbe:
          httpGet: { path: /health, port: 80 }
          initialDelaySeconds: 120
          periodSeconds: 5
        resources:
          limits:
            cpu: "${LLMDBENCH_VLLM_COMMON_CPU_NR}"
            memory: ${LLMDBENCH_VLLM_COMMON_CPU_MEM}
            nvidia.com/gpu: "${LLMDBENCH_VLLM_COMMON_GPU_NR}"
            ephemeral-storage: "20Gi"
          requests:
            cpu: "${LLMDBENCH_VLLM_COMMON_CPU_NR}"
            memory: ${LLMDBENCH_VLLM_COMMON_CPU_MEM}
            nvidia.com/gpu: "${LLMDBENCH_VLLM_COMMON_GPU_NR}"
            ephemeral-storage: "10Gi"
        volumeMounts:
        - name: cache-volume
          mountPath: ${LLMDBENCH_VLLM_COMMON_PVC_MOUNTPOINT}
        - name: shm
          mountPath: /dev/shm
      volumes:
      - name: cache-volume
        persistentVolumeClaim:
          claimName: ${LLMDBENCH_MODEL2PARAM[${model}:pvc]}
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

    announce "Deploying model \"${model}\" (from files located at $LLMDBENCH_CONTROL_WORK_DIR)..."

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml
apiVersion: v1
kind: Service
metadata:
  name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
  type: ClusterIP
EOF

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    if [[ ${LLMDBENCH_VLLM_STANDALONE_HTTPROUTE} -eq 1 ]]; then
      cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  parentRefs:
  - name: openshift-gateway
    namespace: openshift-gateway
  hostnames:
  - "${model}.${LLMDBENCH_CLUSTER_NAMESPACE}.apps.${LLMDBENCH_CLUSTER_URL#https://api.}"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
      port: 80
EOF

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
  done

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "ℹ️  Waiting for (standalone) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod -l app=vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    announce "ℹ️  Waiting for (standalone) pods serving ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l app=vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    is_route=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} get route --ignore-not-found | grep vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}-route || true)
    if [[ -z $is_route ]]
    then
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} expose service/vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} --name=vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}-route" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
    announce "ℹ️  vllm (standalone) ${model} Ready"
  done

  announce "A snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_CLUSTER_NAMESPACE}\":"
  if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 0 ]]; then
    ${LLMDBENCH_CONTROL_KCMD} get --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} deployment,service,httproute,route,pods,secrets
  fi
else
  announce "ℹ️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi