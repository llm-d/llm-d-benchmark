#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

announce "🔍 Checking if Gateway Provider is setup..."
if [[ $LLMDBENCH_USER_IS_ADMIN -eq 1 ]]; then
  if [[ $(${LLMDBENCH_CONTROL_KCMD} get pods -n kgateway-system --no-headers --ignore-not-found --field-selector status.phase=Running | wc -l) -ne 0 ]]; then
    announce "⏭️  Gateway Provider is already setup, skipping installation"
  else
    llmd_opts="--infra-only --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE}"
    announce "🚀 Calling llm-d-deployer with options \"${llmd_opts}\"..."
    pushd $LLMDBENCH_DEPLOYER_DIR/llm-d-deployer/quickstart &>/dev/null
    llmdbench_execute_cmd "cd $LLMDBENCH_DEPLOYER_DIR/llm-d-deployer/quickstart; export KUBECONFIG=$LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx; export HF_TOKEN=$LLMDBENCH_HF_TOKEN; ./llmd-installer.sh $llmd_opts" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
    popd &>/dev/null
    announce "✅ llm-d-deployer prepared namespace"
  fi

  wiev1=$(${LLMDBENCH_CONTROL_KCMD} get crd -o "custom-columns=NAME:.metadata.name,VERSIONS:spec.versions[*].name" | grep -E "workload.*istio.*v1," || true)
  if [[ -z ${wiev1} ]]; then
    announce "📜 Applying more recent CRDs (v1.23.1) from istio..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f https://raw.githubusercontent.com/istio/istio/refs/tags/1.23.1/manifests/charts/base/crds/crd-all.gen.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0 3
    announce "✅ More recent CRDs from istio applied successfully"

  else
    announce "⏭️  The CRDs from istio present are recent enough, skipping application of newer CRDs"
  fi
else
    announce "❗No privileges to setup Gateway Provider. Will assume an user with proper privileges already performed this action."
fi
