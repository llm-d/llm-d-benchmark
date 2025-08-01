#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then

  announce "🔍 Ensuring gateway infrastructure (${LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME}) is setup..."
  has_helm_infra_chart=$($LLMDBENCH_CONTROL_HCMD list | grep infra-$LLMDBENCH_VLLM_MODELSERVICE_RELEASE || true)
  if [[ $LLMDBENCH_USER_IS_ADMIN -eq 1 ]]; then
    llmd_opts="--namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --gateway ${LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME} --context $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx --release infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}"
    announce "🚀 Calling llm-d-infra with options \"${llmd_opts}\"..."
    pushd $LLMDBENCH_INFRA_DIR/llm-d-infra/quickstart &>/dev/null
    llmdbench_execute_cmd "export HF_TOKEN=$LLMDBENCH_HF_TOKEN; ./llmd-infra-installer.sh $llmd_opts" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    popd &>/dev/null

    announce "✅ llm-d-infra prepared namespace"

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

else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
