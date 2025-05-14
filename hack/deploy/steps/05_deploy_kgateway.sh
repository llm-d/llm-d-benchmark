#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_USER_IS_ADMIN -eq 1 ]]; then
  announce "Setting up inference-gateway using KGateway..."
  if [[ $(${LLMDBENCH_CONTROL_KCMD} get pods -n kgateway-system --no-headers --ignore-not-found  --field-selector status.phase=Running | wc -l) -ne 0 ]]; then
    echo "❗ KGateway already installed."
  else
    pushd ${LLMDBENCH_GAIE_DIR} &>/dev/null
    if [[ ! -d gateway-api-inference-extension ]]; then
        llmdbench_execute_cmd "git clone https://github.com/neuralmagic/gateway-api-inference-extension.git" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
    pushd gateway-api-inference-extension &>/dev/null
    llmdbench_execute_cmd "INFRASTRUCTURE_OVERRIDE=true make environment.dev.kubernetes.infrastructure" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    popd &>/dev/null
    popd &>/dev/null
  fi
  _wiev1=$(${LLMDBENCH_CONTROL_KCMD} get crd -o "custom-columns=NAME:.metadata.name,VERSIONS:spec.versions[*].name" | grep -E "workload.*istio.*v1," || true)
  if [[ -z ${_wiev1} ]]; then
    announce "Installing the latest CRDs from istio..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f https://raw.githubusercontent.com/istio/istio/refs/tags/1.23.1/manifests/charts/base/crds/crd-all.gen.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  else
    announce "ℹ️ Latest CRDs from istio already installed"
  fi
else
    announce "❗No privileges to setup KGateway. Will assume an user with proper privileges already performed this action."
fi