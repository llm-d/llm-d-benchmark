#!/usr/bin/env bash

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/)

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../)
export LLMDBENCH_CONTROL_CALLER=$(echo $0 | rev | cut -d '/' -f 1 | rev)

export LLMDBENCH_STEPS_DIR="$LLMDBENCH_CONTROL_DIR/steps"

source ${LLMDBENCH_CONTROL_DIR}/env.sh

export LLMDBENCH_CONTROL_DEEP_CLEANING=${LLMDBENCH_CONTROL_DEEP_CLEANING:-0}
export LLMDBENCH_CONTROL_DRY_RUN=${LLMDBENCH_CONTROL_DRY_RUN:-0}
export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=
export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=


function show_usage {
    echo -e "Usage: ${LLMDBENCH_CONTROL_CALLER} -t/--type [list of environment types targeted for cleaning (default=$LLMDBENCH_DEPLOY_METHODS)) \n \
              -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO) ] \n \
              -d/--deep [\"deep cleaning\"] (default=$LLMDBENCH_CONTROL_DEEP_CLEANING) ] \n \
              -p/--namespace [namespace where to deploy (default=$LLMDBENCH_VLLM_COMMON_NAMESPACE)] \n \
              -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
              -r/--release [deployer helm chart release name (default=$LLMDBENCH_VLLM_DEPLOYER_RELEASE)] \n \
              -m/--models [list the models to be deployed (default=$LLMDBENCH_DEPLOY_MODEL_LIST) ] \n \
              -t/--methods [list the methods employed to carry out the deployment (default=$LLMDBENCH_DEPLOY_METHODS, possible values \"standalone\" and \"deployer\") ] \n \
              -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE) ] \n \
              -h/--help (show this help) \n\

              * [models] can be specified with a full name (e.g., \"ibm-granite/granite-3.3-2b-instruct\") or as an alias. The following aliases are available \n\
                  - llama-3b -> meta-llama/Llama-3.2-3B-Instruct \n\
                  - llama-8b -> meta-llama/Llama-3.1-8B-Instruct \n\
                  - llama-17b -> RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic \n\
                  - llama-70b -> meta-llama/Llama-3.1-70B-Instruct"
}

while [[ $# -gt 0 ]]; do
    key="$1"

    case $key in
        -m=*|--models=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_MODEL_LIST=$(echo $key | cut -d '=' -f 2)
        ;;
        -m|--models)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_MODEL_LIST="$2"
        shift
        ;;
        -p=*|--namespace=*)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE=$(echo $key | cut -d '=' -f 2)
        ;;
        -p|--namespace)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE="$2"
        shift
        ;;
        -c=*|--scenario=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=$(echo $key | cut -d '=' -f 2)
        ;;
        -c|--scenario)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO="$2"
        shift
        ;;
        -t=*|--methods=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS=$(echo $key | cut -d '=' -f 2)
        ;;
        -t|--methods)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS="$2"
        shift
        ;;
        -r=*|--release=*)
        export LLMDBENCH_CLIOVERRIDE_VLLM_DEPLOYER_RELEASE=$(echo $key | cut -d '=' -f 2)
        ;;
        -r|--release)
        export LLMDBENCH_CLIOVERRIDE_VLLM_DEPLOYER_RELEASE="$2"
        shift
        ;;
        -d|--deep)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DEEP_CLEANING=1
        ;;
        -n|--dry-run)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DRY_RUN=1
        ;;
        -v|--verbose)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_VERBOSE=1
        ;;
        -h|--help)
        show_usage
        if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
        then
            exit 0
        else
            return 0
        fi
        ;;
        *)
        echo "ERROR: unknown option \"$key\""
        show_usage
        exit 1
        ;;
        esac
        shift
done

export LLMDBENCH_CONTROL_CLI_OPTS_PROCESSED=1

source ${LLMDBENCH_CONTROL_DIR}/env.sh

extract_environment
sleep 5

source ${LLMDBENCH_STEPS_DIR}/00_ensure_llm-d-infra.sh

for resource in ${LLMDBENCH_CONTROL_RESOURCE_LIST//,/ }; do
  has_resource=$($LLMDBENCH_CONTROL_KCMD get ${resource} --no-headers -o name 2>&1 | grep error || true)
  if [[ ! -z ${has_resource} ]]; then
    export LLMDBENCH_CONTROL_RESOURCE_LIST=$(echo ${LLMDBENCH_CONTROL_RESOURCE_LIST} | $LLMDBENCH_CONTROL_SCMD -e "s/${resource},/,/g" -e 's/,,/,/g' -e 's/^,//')
  fi
done

announce "🧹 Cleaning up namespace: $LLMDBENCH_VLLM_COMMON_NAMESPACE"

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_DEPLOYER_ACTIVE -eq 1 ]]; then

  for chartname in $($LLMDBENCH_CONTROL_HCMD list --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --output json | jq -r '.[].name'); do
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_HCMD} uninstall $chartname --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done

  if [[ $LLMDBENCH_CONTROL_DEEP_CLEANING -eq 0 ]]; then
    hclist=$($LLMDBENCH_CONTROL_HCMD --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE list --no-headers | grep llm-d || true)
    hclist=$(echo "${hclist}" | awk '{ print $1 }')
    for hc in ${hclist}; do
      announce "🗑️  Deleting Helm release \"${hc}\"..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_HCMD} uninstall ${hc} --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Helm release \"${hc}\" fully deleted."
    done
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE --ignore-not-found=true route llm-d-inference-gateway-route" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE --ignore-not-found=true job download-model" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  else
    for tgtns in ${LLMDBENCH_VLLM_COMMON_NAMESPACE} ${LLMDBENCH_HARNESS_NAMESPACE}; do
      for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do
        cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/teardown.yaml
sampleApplication:
  enabled: true
  baseConfigMapRefName: basic-gpu-with-nixl-and-redis-lookup-preset
  model:
    modelArtifactURI: pvc://$LLMDBENCH_VLLM_COMMON_PVC_NAME/models/$(model_attribute $model model)
    modelName: "$(model_attribute $model model)"
EOF
        llmd_opts="--skip-infra --uninstall --namespace $tgtns --values-file $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/teardown.yaml --context $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx"
        announce "🚀 Calling llm-d-deployer with options \"${llmd_opts}\"..."
        llmdbench_execute_cmd "cd $LLMDBENCH_DEPLOYER_DIR/llm-d-deployer/quickstart; export HF_TOKEN=$LLMDBENCH_HF_TOKEN; ./llmd-installer.sh --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --storage-class ${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS} --storage-size ${LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE} $llmd_opts" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ llm-d-deployer completed uninstall"
      done
    done
  fi

  for crb in $(${LLMDBENCH_CONTROL_KCMD} get ClusterRoleBinding | grep ${LLMDBENCH_VLLM_DEPLOYER_RELEASE} | awk '{ print $1}'); do
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete --ignore-not-found=true ClusterRoleBinding $crb" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done

  for cr in ${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-modelservice-endpoint-picker ${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-modelservice-epp-metrics-scrape ${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-modelservice-manager ${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-modelservice-metrics-auth ${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-modelservice-admin ${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-modelservice-editor ${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-modelservice-viewer; do
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete --ignore-not-found=true ClusterRole $cr" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done

  for workload_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete ConfigMap $workload_type-profiles --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  done
fi

if [[ $LLMDBENCH_CONTROL_DEEP_CLEANING -eq 0 ]]; then

  for tgtns in ${LLMDBENCH_VLLM_COMMON_NAMESPACE} ${LLMDBENCH_HARNESS_NAMESPACE}; do
    allres=$(${LLMDBENCH_CONTROL_KCMD} --namespace $tgtns get ${LLMDBENCH_CONTROL_RESOURCE_LIST} -o name)
    tgtres=$(echo "$allres" | grep -Ev "configmap/kube-root-ca.crt|configmap/odh-trusted-ca-bundle|configmap/openshift-service-ca.crt|secret/${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}" || true)

    if [[ ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE} -eq 1 && ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_DEPLOYER_ACTIVE} -eq 0 ]]; then
      tgtres=$(echo "$tgtres" | grep -E "standalone|download-model|testinference|fmperf|lmbenchmark" || true)
    fi

    if [[ ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE} -eq 0 && ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_DEPLOYER_ACTIVE} -eq 1 ]]; then
      tgtres=$(echo "$tgtres" | grep -E "p2p|inference-gateway|inferencepool|llm-route|base-model|endpoint-picker|inference-route|inference-gateway-secret|inference-gateway-params|inference-gateway|fmperf|lmbenchmark" || true)
    fi

    for delres in $tgtres; do
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete --namespace $tgtns --ignore-not-found=true $delres" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    done

  done
else
  RESOURCE_KINDS=(
  deployment
  service
  secret
  gateway
  httproute
  route
  inferencemodel
  inferencepool
  configmap
  job
  role
  rolebinding
  serviceaccount
  pod
  pvc
)

  for tgtns in ${LLMDBENCH_VLLM_COMMON_NAMESPACE} ${LLMDBENCH_HARNESS_NAMESPACE}; do
    for kind in "${RESOURCE_KINDS[@]}"; do
      announce "🗑️ Deleting all $kind in namespace $tgtns..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete "$kind" --all -n "$tgtns" --ignore-not-found=true || true" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    done
  done
fi

if [[ $LLMDBENCH_CONTROL_DEEP_CLEANING -eq 1 ]]; then
# Optional: delete cloned repos if they exist
  announce "🧼 Cleaning up local Git clones..."
  sleep 10
  llmdbench_execute_cmd "rm -rf ${LLMDBENCH_DEPLOYER_DIR}/llm-d-deployer ${LLMDBENCH_HARNESS_DIR}/fmperf" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
fi

announce "✅ Cleanup complete. Namespaces \"${LLMDBENCH_VLLM_COMMON_NAMESPACE},${LLMDBENCH_HARNESS_NAMESPACE}\" are now cleared (except shared cluster-scoped resources like Gateway Provider)."
