#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then

  extract_environment

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_${model}.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)
  labels:
    app: vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  replicas: ${LLMDBENCH_VLLM_COMMON_REPLICAS}
  selector:
    matchLabels:
      app: vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)
  template:
    metadata:
      labels:
        app: vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)
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
      - name: vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)
        image: ${LLMDBENCH_VLLM_STANDALONE_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve $(model_attribute $model model)
          --port ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
          --max-model-len ${LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN}
          --disable-log-requests
          --gpu-memory-utilization $LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL
          --tensor-parallel-size $LLMDBENCH_VLLM_COMMON_GPU_NR
        env:
        - name: HF_HOME
          value: ${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT}
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
              key: HF_TOKEN
        - name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
          value: "1"
        ports:
        - containerPort: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
        livenessProbe:
          httpGet: { path: /health, port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT} }
          initialDelaySeconds: 120
          periodSeconds: 10
        readinessProbe:
          httpGet: { path: /health, port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT} }
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
          mountPath: ${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT}
        - name: shm
          mountPath: /dev/shm
      volumes:
      - name: cache-volume
        persistentVolumeClaim:
          claimName: ${LLMDBENCH_VLLM_COMMON_PVC_NAME}
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

    announce "🚚 Deploying model \"${model}\" and associated service (from files located at $LLMDBENCH_CONTROL_WORK_DIR)..."

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml
apiVersion: v1
kind: Service
metadata:
  name: vllm-standalone-$(model_attribute $model label)
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)
  type: ClusterIP
EOF

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    if [[ ${LLMDBENCH_VLLM_STANDALONE_HTTPROUTE} -eq 1 ]]; then
      cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: vllm-standalone-$(model_attribute $model label)
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
    - name: vllm-standalone-$(model_attribute $model parameters)-vllm-$$(model_attribute $model label)-$(model_attribute $model type)
      port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
EOF

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
    announce "✅ Model \"${model}\" and associated service deployed."
  done

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "⏳ Waiting for (standalone) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod -l app=vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (standalone) pods serving model ${model} running"

    announce "⏳ Waiting for (standalone) pods serving ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l app=vllm-standalone-$(model_attribute $model parameters)-vllm-$(model_attribute $model label)-$(model_attribute $model type)" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (standalone) pods serving model ${model} ready"

    if [[ $LLMDBENCH_VLLM_STANDALONE_ROUTE -ne 0 ]]; then
      is_route=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} get route --ignore-not-found | grep vllm-standalone-$(model_attribute $model label)-route || true)
      if [[ -z $is_route ]]
      then
        announce "📜 Exposing pods serving model ${model} as service..."
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} expose service/vllm-standalone-$(model_attribute $model label) --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} --name=vllm-standalone-$(model_attribute $model label)-route" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ Service for pods service model ${model} created"
      fi
      announce "✅ Model \"${model}\" and associated service deployed."
    fi
  done

  announce "ℹ️ A snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_CLUSTER_NAMESPACE}\":"
  if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 0 ]]; then
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} get --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} deployment,service,httproute,route,pods,secrets" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
  fi
else
  announce "⏭️  Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
