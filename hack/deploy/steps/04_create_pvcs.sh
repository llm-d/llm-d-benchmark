#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
    announce "Creating PVC for caching model ${model}..."
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${model}.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${LLMDBENCH_MODEL2PARAM[${model}:pvc]}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE}
  storageClassName: ${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS}
EOF
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${model}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done

  for vol in ${LLMDBENCH_VLLM_COMMON_PVC_NAME}; do
    announce "Creating PVC ${vol} for caching models..."
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${vol}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE}
  storageClassName: ${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS}
EOF
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done

  for vol in ${LLMDBENCH_FMPERF_PVC_NAME}; do
    announce "Creating PVC ${vol} for fmperf data storage..."
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${vol}
  namespace: ${LLMDBENCH_CLUSTER_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_FMPERF_PVC_SIZE}
  storageClassName: ${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS}
EOF
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done

else
  announce "ℹ️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
