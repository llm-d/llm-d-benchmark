#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then

  extract_environment

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    modelfn=$(echo ${model} | ${LLMDBENCH_CONTROL_SCMD} 's^/^___^g' )
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_${modelfn}.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-standalone-$(model_attribute $model label)
  labels:
    app: vllm-standalone-$(model_attribute $model label)
  namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
spec:
  replicas: ${LLMDBENCH_VLLM_COMMON_REPLICAS}
  selector:
    matchLabels:
      app: vllm-standalone-$(model_attribute $model label)
  template:
    metadata:
      labels:
        app: vllm-standalone-$(model_attribute $model label)
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
      - name: vllm-standalone-$(model_attribute $model label)
        image: $(get_image ${LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY} ${LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO} ${LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME} ${LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG})
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          $(render_string $LLMDBENCH_VLLM_STANDALONE_ARGS $model)
        env:
        - name: HF_HOME
          value: ${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT}
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
              key: HF_TOKEN
        $(add_additional_env_to_yaml)
        ports:
        - containerPort: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
        startupProbe:
          httpGet:
            path: /health
            port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
          failureThreshold: 200
          initialDelaySeconds: ${LLMDBENCH_VLLM_STANDALONE_INITIAL_DELAY_PROBE}
          periodSeconds: 30
          timeoutSeconds: 5
        livenessProbe:
          tcpSocket:
            port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
          failureThreshold: 3
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
          failureThreshold: 3
          periodSeconds: 5
        resources:
          limits:
            cpu: "${LLMDBENCH_VLLM_COMMON_CPU_NR}"
            memory: ${LLMDBENCH_VLLM_COMMON_CPU_MEM}
            $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"${LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR}\"")
            ephemeral-storage: ${LLMDBENCH_VLLM_STANDALONE_EPHEMERAL_STORAGE}
          requests:
            cpu: "${LLMDBENCH_VLLM_COMMON_CPU_NR}"
            memory: ${LLMDBENCH_VLLM_COMMON_CPU_MEM}
            $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"${LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR}\"")
            ephemeral-storage: ${LLMDBENCH_VLLM_STANDALONE_EPHEMERAL_STORAGE}
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

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_${modelfn}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${modelfn}.yaml
apiVersion: v1
kind: Service
metadata:
  name: vllm-standalone-$(model_attribute $model label)
  namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
  selector:
    app: vllm-standalone-$(model_attribute $model label)
  type: ClusterIP
EOF

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${modelfn}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    srl=deployment,service,route,pods,secrets
    if [[ ${LLMDBENCH_VLLM_STANDALONE_HTTPROUTE} -eq 1 ]]; then
      srl=deployment,service,httproute,route,pods,secrets
      cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${modelfn}.yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: vllm-standalone-$(model_attribute $model label)
  namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
spec:
  parentRefs:
  - name: openshift-gateway
    namespace: openshift-gateway
  hostnames:
  - "${model}.${LLMDBENCH_VLLM_COMMON_NAMESPACE}.apps.${LLMDBENCH_CLUSTER_URL#https://api.}"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: vllm-standalone-$(model_attribute $model parameters)-vllm-$$(model_attribute $model label)-$(model_attribute $model type)
      port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
EOF

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${modelfn}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
    announce "✅ Model \"${model}\" and associated service deployed."
  done

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "⏳ Waiting for (standalone) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_VLLM_COMMON_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_VLLM_COMMON_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod -l app=vllm-standalone-$(model_attribute $model label)" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (standalone) pods serving model ${model} running"

    announce "⏳ Waiting for (standalone) pods serving ${model} to be Ready (timeout=${LLMDBENCH_VLLM_COMMON_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_VLLM_COMMON_TIMEOUT}s --for=condition=Ready=True pod -l app=vllm-standalone-$(model_attribute $model label)" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (standalone) pods serving model ${model} ready"

    if [[ $LLMDBENCH_VLLM_STANDALONE_ROUTE -ne 0 && $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -ne 0 ]]; then
      is_route=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} get route --ignore-not-found | grep vllm-standalone-$(model_attribute $model label)-route || true)
      if [[ -z $is_route ]]
      then
        announce "📜 Exposing pods serving model ${model} as service..."
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} expose service/vllm-standalone-$(model_attribute $model label) --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --target-port=${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT} --name=vllm-standalone-$(model_attribute $model label)-route" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ Service for pods service model ${model} created"
      fi
      announce "✅ Model \"${model}\" and associated service deployed."
    fi
  done

  announce "ℹ️ A snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_VLLM_COMMON_NAMESPACE}\":"
  if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 0 ]]; then
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} get --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} $srl" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
  fi
else
  announce "⏭️  Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
