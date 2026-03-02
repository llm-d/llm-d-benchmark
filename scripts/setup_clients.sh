#!/usr/bin/env bash
# Setup client tools required for llm-d-benchmark

set -euo pipefail

HELM_VERSION="v3.16.4"

function install_helm {
    local version="${1:-${HELM_VERSION}}"
    curl -fsSL -o get_helm.sh "https://raw.githubusercontent.com/helm/helm/${version}/scripts/get-helm-3"
    chmod 700 get_helm.sh
    DESIRED_VERSION="${version}" sudo --preserve-env=DESIRED_VERSION ./get_helm.sh
    rm -f get_helm.sh
}

install_helm
