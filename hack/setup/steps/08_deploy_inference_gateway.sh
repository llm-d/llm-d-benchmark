#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

cat <<EOF | ${LLMDBENCH_KCMD} apply -f -
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

cat <<EOF | ${LLMDBENCH_KCMD} apply -f -
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

cat <<EOF | ${LLMDBENCH_KCMD} apply -f -
apiVersion: gateway.networking.k8s.io/v1beta1
kind: HTTPRoute
metadata:
  name: inference-http-route
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
      name: vllm-llama3-8b-instruct
      port: 8000
      weight: 1
    matches:
    - path:
        type: PathPrefix
        value: /
    timeouts:
      request: 300s
EOF

if ${LLMDBENCH_KCMD} get svc -n "$LLMDBENCH_OPENSHIFT_NAMESPACE" | grep -q 'LoadBalancer'; then
  echo "⚠️ Found LoadBalancer service — patching to enforce ClusterIP..."
  ${LLMDBENCH_KCMD} patch gateway inference-gateway -n "$LLMDBENCH_OPENSHIFT_NAMESPACE" --type=merge -p '{
    "spec": {
      "infrastructure": {
        "parametersRef": {
          "group": "gateway.kgateway.dev",
          "kind":  "GatewayParameters",
          "name":  "inference-gateway-params"
        }
      }
    }
  }'
fi
