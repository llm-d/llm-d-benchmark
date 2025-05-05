#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "Setting up inference-gateway using KGateway..."
if [[ $(${LLMDBENCH_KCMD} get pods -n kgateway-system --no-headers --ignore-not-found  --field-selector status.phase=Running | wc -l) -ne 0 ]]; then
  echo "❗ KGateway already installed. Skipping install."
else
  pushd ${LLMDBENCH_GAIE_DIR} &>/dev/null
  if [[ ! -d gateway-api-inference-extension ]]; then
      git clone https://github.com/neuralmagic/gateway-api-inference-extension.git
  fi
  pushd gateway-api-inference-extension &>/dev/null
  INFRASTRUCTURE_OVERRIDE=true make environment.dev.kubernetes.infrastructure
  popd &>/dev/null
  popd &>/dev/null
fi

_wiev1=$(${LLMDBENCH_KCMD} get crd -o "custom-columns=NAME:.metadata.name,VERSIONS:spec.versions[*].name" | grep -E "workload.*istio.*v1," || true)
if [[ -z ${_wiev1} ]]; then
  echo "Installing the latest crds from istio..."
  ${LLMDBENCH_KCMD} apply -f https://raw.githubusercontent.com/istio/istio/refs/tags/1.23.1/manifests/charts/base/crds/crd-all.gen.yaml &>/dev/null;
fi