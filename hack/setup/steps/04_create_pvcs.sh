#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Creating PVCs for model cache..."
for model in llama-8b llama-70b; do
  oc apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${model}-cache
  namespace: ${OPENSHIFT_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${MODEL_CACHE_SIZE}
  storageClassName: ${STORAGE_CLASS}
EOF
done
