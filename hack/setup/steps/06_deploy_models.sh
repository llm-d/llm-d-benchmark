#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
  if [[ ${model} == llama-8b ]]; then
    echo "Deploying LLaMA 2 8B..."

    ${LLMDBENCH_KCMD} apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  labels:
    app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  template:
    metadata:
      labels:
        app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
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
      - name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
        image: ${LLMDBENCH_MODEL_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve ${LLMDBENCH_MODEL2PARAM[${model}:name]} --port 80
          --disable-log-requests --gpu-memory-utilization 0.95
        env:
        - name: HF_HOME
          value: /root/.cache/huggingface
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-token
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
          claimName: llama-8b-cache
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

    ${LLMDBENCH_KCMD} apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  type: ClusterIP
EOF

    ${LLMDBENCH_KCMD} apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  parentRefs:
  - name: openshift-gateway
    namespace: openshift-gateway
  hostnames:
  - "llama8b.${LLMDBENCH_OPENSHIFT_NAMESPACE}.apps.${LLMDBENCH_OPENSHIFT_HOST#https://api.}"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
      port: 80
EOF
  fi

  if [[ ${model} == llama-70b ]]; then
    echo "Deploying LLaMA 3 70B..."

    ${LLMDBENCH_KCMD} apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  labels:
    app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  template:
    metadata:
      labels:
        app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
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
      - name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
        image: ${LLMDBENCH_MODEL_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve ${LLMDBENCH_MODEL2PARAM[${model}:name]} --port 80 --max-model-len 20000
          --disable-log-requests --gpu-memory-utilization 0.95 --tensor-parallel-size 2
        env:
        - name: HF_HOME
          value: /root/.cache/huggingface
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-token
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
          claimName: llama-70b-cache
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 8Gi
EOF

    ${LLMDBENCH_KCMD} apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  type: ClusterIP
EOF

    ${LLMDBENCH_KCMD} apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  parentRefs:
  - name: openshift-gateway
    namespace: openshift-gateway
  hostnames:
  - "llama70b.${LLMDBENCH_OPENSHIFT_NAMESPACE}.apps.${LLMDBENCH_OPENSHIFT_HOST#https://api.}"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: ${LLMDBENCH_MODEL2PARAM[${model}:label]}
      port: 80
EOF

  fi

done
