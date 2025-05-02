#!/usr/bin/env bash

set -euo pipefail

NAMESPACE="${OPENSHIFT_NAMESPACE:-}"
if [[ -z "$NAMESPACE" ]]; then
  echo "❌ Please export OPENSHIFT_NAMESPACE before running this script."
  exit 1
fi

echo "🧹 Cleaning up namespace: $NAMESPACE"

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
  echo "🗑️  Deleting all $kind in namespace $NAMESPACE..."
  oc delete "$kind" --all -n "$NAMESPACE" --ignore-not-found=true || true
done

# Special case: Helm release (if used)
echo "🧽 Deleting Helm release: vllm-p2p (if exists)..."
helm uninstall vllm-p2p -n "$NAMESPACE" || true

# Optional: delete cloned repos if they exist
echo "🧼 Cleaning up local Git clones..."
rm -rf llm-d-kv-cache-manager gateway-api-inference-extension fmperf

echo "✅ Cleanup complete. Namespace '$NAMESPACE' is now cleared (except shared cluster-scoped resources like kgateway)."
