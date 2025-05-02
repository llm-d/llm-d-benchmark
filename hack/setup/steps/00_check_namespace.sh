#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "🔍 Checking if namespace '${OPENSHIFT_NAMESPACE}' exists..."

if ! oc get namespace "$OPENSHIFT_NAMESPACE" --ignore-not-found | grep -q "$OPENSHIFT_NAMESPACE"; then
  echo "⚠️  Namespace '${OPENSHIFT_NAMESPACE}' not found. Stopping..."
  exit 1
#   oc create namespace "$OPENSHIFT_NAMESPACE"
else
  echo "✅ Namespace '${OPENSHIFT_NAMESPACE}' exists."
fi
