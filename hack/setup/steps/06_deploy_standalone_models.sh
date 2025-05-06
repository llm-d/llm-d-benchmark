#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_a_deployment_llama-8b.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}
  labels:
    app: standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}
  template:
    metadata:
      labels:
        app: standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: nvidia.com/gpu.product
                operator: In
                values:
                - $LLMDBENCH_GPU_MODEL
      containers:
      - name: standalone-${LLMDBENCH_MODEL2PARAM[llama-8b:label]}
        image: ${LLMDBENCH_MODEL_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve ${LLMDBENCH_MODEL2PARAM[llama-8b:name]} --port 80
          --disable-log-requests --gpu-memory-utilization 0.95
        env:
        - name: HF_HOME
          value: /root/.cache/huggingface
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: standalone-hf-token
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
            cpu: "6"
            memory: 80Gi
            nvidia.com/gpu: "1"
            ephemeral-storage: "20Gi"
          requests:
            cpu: "1"
            memory: 60Gi
            nvidia.com/gpu: "1"
            ephemeral-storage: "10Gi"
        volumeMounts:
        - name: cache-volume
          mountPath: /root/.cache/huggingface
        - name: shm
          mountPath: /dev/shm
      volumes:
      - name: cache-volume
        persistentVolumeClaim:
          claimName: standalone-llama-8b-cache
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

  cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_a_deployment_llama-70b.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}
  labels:
    app: standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}
  template:
    metadata:
      labels:
        app: standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: nvidia.com/gpu.product
                operator: In
                values:
                - $LLMDBENCH_GPU_MODEL
      containers:
      - name: standalone-${LLMDBENCH_MODEL2PARAM[llama-70b:label]}
        image: ${LLMDBENCH_MODEL_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve ${LLMDBENCH_MODEL2PARAM[llama-70b:name]} --port 80 --max-model-len 20000
          --disable-log-requests --gpu-memory-utilization 0.95 --tensor-parallel-size 2
        env:
        - name: HF_HOME
          value: /root/.cache/huggingface
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: standalone-hf-token
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
            cpu: "10"
            memory: 200Gi
            nvidia.com/gpu: "2"
            ephemeral-storage: "30Gi"
          requests:
            cpu: "2"
            memory: 100Gi
            nvidia.com/gpu: "2"
            ephemeral-storage: "10Gi"
        volumeMounts:
        - name: cache-volume
          mountPath: /root/.cache/huggingface
        - name: shm
          mountPath: /dev/shm
      volumes:
      - name: cache-volume
        persistentVolumeClaim:
          claimName: standalone-llama-70b-cache
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

is_env_type=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep standalone || true)
if [[ ! -z ${is_env_type} ]]
then

  for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
    echo "Deploying model \"${model}\" (from files located at $LLMDBENCH_TEMPDIR)..."

    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_a_deployment_${model}.yaml" ${LLMDBENCH_DRY_RUN}

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml
apiVersion: v1
kind: Service
metadata:
  name: standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  type: ClusterIP
EOF

    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_b_service_${model}.yaml" ${LLMDBENCH_DRY_RUN}

    cat << EOF > $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
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
    - name: standalone-${LLMDBENCH_MODEL2PARAM[${model}:label]}
      port: 80
EOF

    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_TEMPDIR/${LLMDBENCH_CURRENT_STEP}_c_httproute_${model}.yaml" ${LLMDBENCH_DRY_RUN}
  done
else
  echo "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi
echo -e "\nA snapshot of the relevant (model-specific) resources on namespace \"${LLMDBENCH_OPENSHIFT_NAMESPACE}\":\n"
${LLMDBENCH_KCMD} get --namespace ${LLMDBENCH_OPENSHIFT_NAMESPACE} deployment,service,httproute,pods,secrets
