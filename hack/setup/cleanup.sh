#!/usr/bin/env bash

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_DIR=$(realpath $(pwd)/)

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

LLMDBENCH_STEPS_DIR="$LLMDBENCH_DIR/steps"

source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "🧹 Cleaning up namespace: $LLMDBENCH_OPENSHIFT_NAMESPACE"

# List of resource kinds to clean up
RESOURCE_KINDS=(
  deployment
  service
  secret
  pvc
  gateway
  httproute
  inferencemodel
  inferencepool
  configmap
  job
  role
  rolebinding
  serviceaccount
  pod
)

# Delete each resource type (ignoring not found errors)
for kind in "${RESOURCE_KINDS[@]}"; do
  echo "🗑️  Deleting all $kind in namespace $LLMDBENCH_OPENSHIFT_NAMESPACE..."
  ${LLMDBENCH_KCMD} delete "$kind" --all -n "$LLMDBENCH_OPENSHIFT_NAMESPACE" --ignore-not-found=true || true
done

# Special case: Helm release (if used)
for model in ${LLMDBENCH_MODEL_LIST//,/ }; do
  echo "🧽 Deleting Helm release: vllm-p2p-${LLMDBENCH_MODEL2PARAM[${model}:params]} (if exists)..."
  ${LLMDBENCH_HCMD} uninstall vllm-p2p -n "$LLMDBENCH_OPENSHIFT_NAMESPACE" || true
done

# Optional: delete cloned repos if they exist
#echo "🧼 Cleaning up local Git clones..."
#rm -rf llm-d-kv-cache-manager gateway-api-inference-extension fmperf

echo "✅ Cleanup complete. Namespace '$LLMDBENCH_OPENSHIFT_NAMESPACE' is now cleared (except shared cluster-scoped resources like kgateway)."
