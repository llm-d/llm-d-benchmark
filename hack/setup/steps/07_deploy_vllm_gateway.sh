#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "Deploying vLLM via Helm with LMCache and Gateway..."

${LLMDBENCH_KCMD} adm policy add-scc-to-user anyuid -z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} -n "$LLMDBENCH_OPENSHIFT_NAMESPACE" || true
${LLMDBENCH_KCMD} adm policy add-scc-to-user anyuid -z inference-gateway -n "$LLMDBENCH_OPENSHIFT_NAMESPACE" || true

pushd ${LLMDBENCH_KVCM_DIR} &>/dev/null
if [[ ! -d llm-d-kv-cache-manager ]]; then
  git clone https://github.com/neuralmagic/llm-d-kv-cache-manager.git || true
fi

pushd llm-d-kv-cache-manager/vllm-setup-helm &>/dev/null
for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
  echo "Installing release vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]}..."
  ${LLMDBENCH_HCMD} upgrade --install vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]} . \
  --namespace "$LLMDBENCH_OPENSHIFT_NAMESPACE" \
  --set secret.create=true \
  --set secret.hfTokenValue="${LLMDBENCH_HF_TOKEN}" \
  --set vllm.replicaCount=${LLMDBENCH_VLLM_REPLICAS} \
  --set persistence.enabled=${LLMDBENCH_VLLM_PERSISTENCE_ENABLED} \
  --set persistence.accessMode=ReadWriteMany \
  --set persistence.size=${LLMDBENCH_MODEL_CACHE_SIZE} \
  --set persistence.storageClassName=${LLMDBENCH_STORAGE_CLASS} \
  --set vllm.poolLabelValue="vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}" \
  --set vllm.model.name=${LLMDBENCH_MODEL2PARAM[${model}:name]} \
  --set vllm.model.label=${LLMDBENCH_MODEL2PARAM[${model}:label]}
done
popd &>/dev/null

VERSION="v0.3.0"
${LLMDBENCH_KCMD} apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${VERSION}/manifests.yaml"

pushd ${LLMDBENCH_GAIE_DIR} &>/dev/null
if [[ ! -d gateway-api-inference-extension ]]; then
    git clone https://github.com/neuralmagic/gateway-api-inference-extension.git
fi
pushd gateway-api-inference-extension &>/dev/null

${LLMDBENCH_KCMD} apply -f config/manifests/gateway/kgateway/gateway.yaml
${LLMDBENCH_KCMD} apply -f config/manifests/gateway/kgateway/httproute.yaml
${LLMDBENCH_KCMD} apply -f config/manifests/inferencemodel.yaml

${LLMDBENCH_SCMD} -i "s^namespace: .*^namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}^g" config/manifests/inferencepool-resources.yaml
${LLMDBENCH_SCMD} -i "s|image: .*|image: ${LLMDBBENCH_EPP_IMAGE}|g" config/manifests/inferencepool-resources.yaml

${LLMDBENCH_KCMD} apply -f config/manifests/inferencepool-resources.yaml

popd &>/dev/null
popd &>/dev/null