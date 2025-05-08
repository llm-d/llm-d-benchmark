#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_llama-8b.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}-instruct
  labels:
    app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}-instruct
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  replicas: ${LLMDBENCH_VLLM_COMMON_REPLICAS}
  selector:
    matchLabels:
      app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}-instruct
  template:
    metadata:
      labels:
        app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}-instruct
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: nvidia.com/gpu.product
                operator: In
                values:
                - $LLMDBENCH_VLLM_COMMON_GPU_MODEL
      containers:
      - name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}-instruct
        image: ${LLMDBENCH_VLLM_STANDALONE_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve ${LLMDBENCH_MODEL2PARAM[llama-8b:name]} --port 80
          --disable-log-requests --gpu-memory-utilization ${LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL}
        env:
        - name: HF_HOME
          value: ${LLMDBENCH_VLLM_COMMON_PVC_MOUNTPOINT}
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: vllm-common-hf-token
              key: token
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
          claimName: ${LLMDBENCH_MODEL2PARAM[llama-8b:pvc]}
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

  cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_llama-70b.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}-instruct
  labels:
    app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}-instruct
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  replicas: ${LLMDBENCH_VLLM_COMMON_REPLICAS}
  selector:
    matchLabels:
      app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}-instruct
  template:
    metadata:
      labels:
        app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}-instruct
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: nvidia.com/gpu.product
                operator: In
                values:
                - $LLMDBENCH_VLLM_COMMON_GPU_MODEL
      containers:
      - name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:params]}-vllm-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}-instruct
        image: ${LLMDBENCH_VLLM_STANDALONE_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve ${LLMDBENCH_MODEL2PARAM[llama-70b:name]} --port 80 --max-model-len 20000
          --disable-log-requests --gpu-memory-utilization ${LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL} --tensor-parallel-size ${LLMDBENCH_VLLM_COMMON_GPU_NR}
        env:
        - name: HF_HOME
          value: /root/.cache/huggingface
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: vllm-common-hf-token
              key: token
        - name: VLLM_ALLOW_LONG_MAX_MODEL_LEN
          value: "1"
        ports:
        - containerPort: 80
        livenessProbe:
          httpGet: { path: /health, port: 80 }
          initialDelaySeconds: 300
          periodSeconds: 10
        readinessProbe:
          httpGet: { path: /health, port: 80 }
          initialDelaySeconds: 300
          periodSeconds: 5
        resources:
          limits:
            cpu: "${LLMDBENCH_VLLM_COMMON_CPU_NR}"
            memory: "${LLMDBENCH_VLLM_COMMON_CPU_MEM}"
            nvidia.com/gpu: "${LLMDBENCH_VLLM_COMMON_GPU_NR}"
            ephemeral-storage: "30Gi"
          requests:
            cpu: "$LLMDBENCH_VLLM_COMMON_CPU_NR"
            memory: "${LLMDBENCH_VLLM_COMMON_CPU_MEM}"
            nvidia.com/gpu: "${LLMDBENCH_VLLM_COMMON_GPU_NR}"
            ephemeral-storage: "10Gi"
        volumeMounts:
        - name: cache-volume
          mountPath: /root/.cache/huggingface
        - name: shm
          mountPath: /dev/shm
      volumes:
      - name: cache-volume
        persistentVolumeClaim:
          claimName: ${LLMDBENCH_MODEL2PARAM[llama-70b:pvc]}
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then

  extract_environment

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "Deploying model \"${model}\" (from files located at $LLMDBENCH_CONTROL_WORK_DIR)..."

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_a_deployment_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}

    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml
apiVersion: v1
kind: Service
metadata:
  name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct
  type: ClusterIP
EOF

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}

    if [[ ${LLMDBENCH_VLLM_STANDALONE_HTTPROUTE} -eq 1 ]]; then
      cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  parentRefs:
  - name: openshift-gateway
    namespace: openshift-gateway
  hostnames:
  - "${model}.${LLMDBENCH_OPENSHIFT_NAMESPACE}.apps.${LLMDBENCH_OPENSHIFT_HOST#https://api.}"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct
      port: 80
EOF

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}
    fi
  done

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "ℹ️  Waiting for ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l app=vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}

    is_route=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} get route --ignore-not-found | grep vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}-route || true)
    if [[ -z $is_route ]]
    then
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} expose service/vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]} --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} --name=vllm-standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}-route" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}
    fi
    announce "ℹ️  vllm (standalone) ${model} Ready"
  done
else
  announce "ℹ️ Environment types are \"${LLMDBENCH_DEPLOY_ENVIRONMENT_TYPES}\". Skipping this step."
fi
announce "A snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_OPENSHIFT_NAMESPACE}\":"
${LLMDBENCH_CONTROL_KCMD} get --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} deployment,service,httproute,pods,secrets
