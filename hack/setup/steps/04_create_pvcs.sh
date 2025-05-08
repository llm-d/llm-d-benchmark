#!/usr/bin/env bash
source ${LLMDBENCH_DIR}/env.sh

if [[ $LLMDBENCH_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
  for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
    announce "Creating PVC for caching model ${model}..."
    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${model}.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: vllm-standalone-${model}-cache
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_MODEL_CACHE_SIZE}
  storageClassName: ${LLMDBENCH_STORAGE_CLASS}
EOF
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${model}.yaml" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}
  done
  for vol in ${LLMDBENCH_VLLM_PVC_NAME}; do
    announce "Creating PVC ${vol} for caching models..."
    cat << EOF > $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${vol}
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_MODEL_CACHE_SIZE}
  storageClassName: ${LLMDBENCH_STORAGE_CLASS}
EOF
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_WORK_DIR/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml" ${LLMDBENCH_DRY_RUN} ${LLMDBENCH_VERBOSE}
  done

else
  announce "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi
