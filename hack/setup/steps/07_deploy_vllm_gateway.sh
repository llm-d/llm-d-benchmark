#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Deploying vLLM via Helm with LMCache and Gateway..."

oc adm policy add-scc-to-user anyuid -z default -n "$OPENSHIFT_NAMESPACE" || true
oc adm policy add-scc-to-user anyuid -z inference-gateway -n "$OPENSHIFT_NAMESPACE" || true

git clone https://github.com/neuralmagic/llm-d-kv-cache-manager.git || true
cd llm-d-kv-cache-manager/vllm-setup-helm

helm upgrade --install vllm-p2p . \
  --namespace "$OPENSHIFT_NAMESPACE" \
  --set secret.create=true \
  --set secret.hfTokenValue="$HF_TOKEN" \
  --set vllm.replicas=1 \
  --set vllm.poolLabelValue="vllm-llama3-8b-instruct"

cd ../../

VERSION="v0.3.0"
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${VERSION}/manifests.yaml"

git clone https://github.com/neuralmagic/gateway-api-inference-extension.git || true
cd gateway-api-inference-extension

kubectl apply -f config/manifests/gateway/kgateway/gateway.yaml
kubectl apply -f config/manifests/gateway/kgateway/httproute.yaml
kubectl apply -f config/manifests/inferencemodel.yaml

sed -i "s/namespace: default/namespace: ${OPENSHIFT_NAMESPACE}/g" config/manifests/inferencepool-resources.yaml
sed -i "s|image: .*|image: quay.io/vmaroon/gateway-api-inference-extension/epp:kvc-v3|g" config/manifests/inferencepool-resources.yaml

kubectl apply -f config/manifests/inferencepool-resources.yaml

cd ..
