#!/usr/bin/env bash
# Install pinned client tools required by the benchmark harness.

set -euo pipefail

KUBECTL_VERSION="v1.31.4"

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
esac

echo "Installing kubectl ${KUBECTL_VERSION} (${OS}/${ARCH})..."
curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/${OS}/${ARCH}/kubectl" -o /tmp/kubectl
chmod +x /tmp/kubectl
sudo mv /tmp/kubectl /usr/local/bin/kubectl
kubectl version --client
