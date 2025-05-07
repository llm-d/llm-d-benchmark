#!/usr/bin/env bash
source ${LLMDBENCH_DIR}/env.sh

if [[ $LLMDBENCH_ENVIRONMENT_TYPE_P2P_ACTIVE -eq 1 ]]; then
  echo "Deploying vLLM via Helm with LMCache..."

  if [[ $LLMDBENCH_IS_OPENSHIFT -eq 1 ]]
  then
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z ${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT} \
-n $LLMDBENCH_OPENSHIFT_NAMESPACE" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}

    llmdbench_execute_cmd "${LLMDBENCH_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z inference-gateway \
-n $LLMDBENCH_OPENSHIFT_NAMESPACE" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}
  fi

  pushd ${LLMDBENCH_KVCM_DIR} &>/dev/null
  if [[ ! -d llm-d-kv-cache-manager ]]; then
    git clone https://github.com/neuralmagic/llm-d-kv-cache-manager.git || true
  fi

  pushd llm-d-kv-cache-manager/vllm-setup-helm &>/dev/null
  git checkout $LLMDBENCH_KVCM_GIT_BRANCH
  for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
    echo "Installing release vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]}..."
    llmdbench_execute_cmd "${LLMDBENCH_HCMD} upgrade --install vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]} . \
--namespace "$LLMDBENCH_OPENSHIFT_NAMESPACE" \
--set secret.create=true \
--set secret.hfTokenValue=\"${LLMDBENCH_HF_TOKEN}\" \
--set secret.name=vllm-p2p-${model}-secrets \
--set persistence.enabled=${LLMDBENCH_VLLM_PERSISTENCE_ENABLED} \
--set persistence.accessModes={\"ReadWriteMany\"} \
--set persistence.size=${LLMDBENCH_MODEL_CACHE_SIZE} \
--set persistence.storageClassName=${LLMDBENCH_STORAGE_CLASS} \
--set vllm.replicaCount=${LLMDBENCH_VLLM_REPLICAS} \
--set vllm.poolLabelValue="vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}" \
--set vllm.image.repository=\"${LLMDBENCH_VLLM_IMAGE_REPOSITORY}\" \
--set vllm.image.tag=\"${LLMDBENCH_VLLM_IMAGE_TAG}\" \
--set vllm.model.name=${LLMDBENCH_MODEL2PARAM[${model}:name]} \
--set vllm.model.label=${LLMDBENCH_MODEL2PARAM[${model}:label]}-instruct \
--set vllm.gpuMemoryUtilization=${LLMDBENCH_VLLM_GPU_MEM_UTIL} \
--set vllm.model.maxModelLen=${LLMDBENCH_VLLM_MAX_MODEL_LEN} \
--set vllm.tensorParallelSize=${LLMDBENCH_VLLM_GPU_NR} \
--set vllm.resources.limits.\"nvidia\.com/gpu\"=${LLMDBENCH_VLLM_GPU_NR} \
--set vllm.resources.requests.\"nvidia\.com/gpu\"=${LLMDBENCH_VLLM_GPU_NR} \
--set dshm.useEmptyDir=true \
--set dshm.sizeLimit=8Gi" ${LLMDBENCH_DRY_RUN}
  done
  popd &>/dev/null
  popd &>/dev/null
fi
