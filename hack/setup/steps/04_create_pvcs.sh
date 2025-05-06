#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

is_env_type=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep standalone || true)
if [[ ! -z ${is_env_type} ]]
then
  echo "Creating PVCs for model cache..."
  for model in ${LLMDBENCH_MODEL_LIST//,/ }; do

    cat << EOF > $LLMDBENCH_TEMPDIR/04_pvc_${model}.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: standalone-${model}-cache
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_MODEL_CACHE_SIZE}
  storageClassName: ${LLMDBENCH_STORAGE_CLASS}
EOF
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} apply -f $LLMDBENCH_TEMPDIR/04_pvc_${model}.yaml" ${LLMDBENCH_DRY_RUN}
  done
else
  echo "ℹ️ Environment types are \"${LLMDBENCH_ENVIRONMENT_TYPES}\". Skipping this step."
fi
