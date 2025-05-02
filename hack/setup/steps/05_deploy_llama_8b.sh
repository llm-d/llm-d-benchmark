#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Deploying LLaMA 2 8B..."

oc apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llama-2-8b
  labels:
    app: llama-2-8b
  namespace: ${OPENSHIFT_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: llama-2-8b
  template:
    metadata:
      labels:
        app: llama-2-8b
    spec:
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: nvidia.com/gpu.product
                operator: In
                values:
                - NVIDIA-A100-SXM4-80GB
      containers:
      - name: llama-2-8b
        image: ${MODEL_IMAGE}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
        - >
          vllm serve meta-llama/Llama-2-8b-chat-hf --port 80
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

oc apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: llama-2-8b
  namespace: ${OPENSHIFT_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: llama-2-8b
  type: ClusterIP
EOF

oc apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: llama-2-8b
  namespace: ${OPENSHIFT_NAMESPACE}
spec:
  parentRefs:
  - name: openshift-gateway
    namespace: openshift-gateway
  hostnames:
  - "llama8b.${OPENSHIFT_NAMESPACE}.apps.${OPENSHIFT_HOST#https://api.}"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: llama-2-8b
      port: 80
EOF
