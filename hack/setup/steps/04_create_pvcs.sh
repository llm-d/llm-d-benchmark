#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "Creating PVCs for model cache..."
for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
  oc apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${model}-cache
  namespace: ${LLMDBENCH_OPENSHIFT_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_MODEL_CACHE_SIZE}
  storageClassName: ${LLMDBENCH_STORAGE_CLASS}
EOF
done
