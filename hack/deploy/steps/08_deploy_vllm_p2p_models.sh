#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_P2P_ACTIVE -eq 1 ]]; then

  extract_environment

  announce "Deploying vLLM via Helm with LMCache..."

  if [[ $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -eq 1 ]]
  then
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z ${LLMDBENCH_CLUSTER_SERVICE_ACCOUNT} \
-n $LLMDBENCH_CLUSTER_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z inference-gateway \
-n $LLMDBENCH_CLUSTER_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  fi

  pushd ${LLMDBENCH_KVCM_DIR} &>/dev/null
  if [[ ! -d llm-d-kv-cache-manager ]]; then
    llmdbench_execute_cmd "git clone https://github.com/neuralmagic/llm-d-kv-cache-manager.git || true" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  fi

cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_affinity.yaml
vllm:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
            operator: In
            values:
            - $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
EOF

  pushd llm-d-kv-cache-manager/vllm-setup-helm &>/dev/null
  llmdbench_execute_cmd "git checkout $LLMDBENCH_KVCM_GIT_BRANCH" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "Installing release vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]}..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_HCMD} upgrade --install vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]} . \
--namespace "$LLMDBENCH_CLUSTER_NAMESPACE" \
--set secret.create=false \
--set secret.keyPrefix=token \
--set secret.name=$LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME \
--set persistence.enabled=${LLMDBENCH_VLLM_COMMON_PERSISTENCE_ENABLED} \
--set persistence.accessModes={\"ReadWriteMany\"} \
--set persistence.size=${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE} \
--set persistence.name=${LLMDBENCH_VLLM_COMMON_PVC_NAME} \
--set persistence.storageClassName=${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS} \
--set persistence.mountPath=${LLMDBENCH_VLLM_COMMON_PVC_MOUNTPOINT} \
--set startupProbe.initialDelaySeconds=600 \
--set livenessProbe.initialDelaySecons=600 \
--set vllm.replicaCount=${LLMDBENCH_VLLM_COMMON_REPLICAS} \
--set vllm.poolLabelValue="vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}" \
--set vllm.image.repository=\"${LLMDBENCH_VLLM_P2P_IMAGE_REPOSITORY}\" \
--set vllm.image.tag=\"${LLMDBENCH_VLLM_P2P_IMAGE_TAG}\" \
--set vllm.model.name=${LLMDBENCH_MODEL2PARAM[${model}:name]} \
--set vllm.model.label=${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]} \
--set vllm.gpuMemoryUtilization=${LLMDBENCH_VLLM_COMMON_GPU_MEM_UTIL} \
--set vllm.model.maxModelLen=${LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN} \
--set vllm.tensorParallelSize=${LLMDBENCH_VLLM_COMMON_GPU_NR} \
--set vllm.resources.limits.\"nvidia\.com/gpu\"=${LLMDBENCH_VLLM_COMMON_GPU_NR} \
--set vllm.resources.requests.cpu=${LLMDBENCH_VLLM_COMMON_CPU_NR:-10} \
--set vllm.resources.requests.memory=${LLMDBENCH_VLLM_COMMON_CPU_MEM} \
--set vllm.extraEnv.LMCACHE_MAX_LOCAL_CPU_SIZE=${LLMDBENCH_VLLM_P2P_LMCACHE_MAX_LOCAL_CPU_SIZE} \
--set vllm.resources.requests.\"nvidia\.com/gpu\"=${LLMDBENCH_VLLM_COMMON_GPU_NR} \
--set dshm.useEmptyDir=true \
--set dshm.sizeLimit=8Gi \
-f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_affinity.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done
  popd &>/dev/null
  popd &>/dev/null

  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do

    announce "ℹ️  Waiting for (p2p) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod -l app=vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    announce "ℹ️  Waiting for (p2p) pods serving ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l app=vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_service_${model}.yaml
apiVersion: v1
kind: Service
metadata:
  name: vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:label]}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  ports:
  - name: http
    port: 80
    targetPort: 80
  selector:
    app: vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]}-vllm-${LLMDBENCH_MODEL2PARAM[${model}:label]}-${LLMDBENCH_MODEL2PARAM[${model}:type]}
  type: ClusterIP
EOF

    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_service_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    is_route=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} get route --ignore-not-found | grep vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:label]}-route || true)
    if [[ -z $is_route ]]
    then
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} expose service/vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:label]} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} --name=vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:label]}-route" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
    announce "ℹ️  vllm (p2p) ${model} Ready"
  done
fi