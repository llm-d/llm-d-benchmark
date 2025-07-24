#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then

  # make sure llm-d-modelservice helm repo is available
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo add ${LLMDBENCH_VLLM_MODELSERVICE_CHART_NAME} ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY_URL} --force-update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  if [[ $LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION == "auto" ]]; then
    export LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION=$($LLMDBENCH_CONTROL_HCMD search repo ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY} | tail -1 | awk '{print $2}' || true)
    if [[ -z $LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION ]]; then
      announce "❌ Unable to find a version for model service helm chart!"
    fi
  fi

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

  llmdbench_execute_cmd "mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/helmfile.yaml
repositories:
  - name: ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY}
    url: https://llm-d-incubation.github.io/llm-d-modelservice/

releases:
  - name: infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
    chart: ${LLMDBENCH_VLLM_INFRA_CHART_NAME}
    version: ${LLMDBENCH_VLLM_INFRA_CHART_VERSION}
    installed: true
    labels:
      managedBy: llm-d-infra-installer

  - name: ms-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
    chart: ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY}/${LLMDBENCH_VLLM_MODELSERVICE_CHART_NAME}
    version: ${LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION}
    installed: true
    needs:
      -  ${LLMDBENCH_VLLM_COMMON_NAMESPACE}/infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    values:
      - ms-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/values.yaml
    labels:
      managedBy: helmfile

  - name: gaie-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    namespace: ${LLMDBENCH_VLLM_COMMON_NAMESPACE}
    chart: ${LLMDBENCH_VLLM_GAIE_CHART_NAME}
    version: ${LLMDBENCH_VLLM_GAIE_CHART_VERSION}
    installed: true
    needs:
      -  ${LLMDBENCH_VLLM_COMMON_NAMESPACE}/infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}
    values:
      - gaie-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/values.yaml
    labels:
      managedBy: helmfile
EOF
else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
