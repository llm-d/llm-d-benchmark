#!/usr/bin/env bash

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/)

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../../)

source ${LLMDBENCH_CONTROL_DIR}/env.sh

export LLMDBENCH_CONTROL_DEEP_CLEANING=${LLMDBENCH_CONTROL_DEEP_CLEANING:-0}
export LLMDBENCH_CONTROL_DRY_RUN=${LLMDBENCH_CONTROL_DRY_RUN:-0}
export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=

function show_usage {
    echo -e "Usage: $(echo $0 | rev | cut -d '/' -f 1 | rev) -t/--type [list of environment types targeted for cleaning (default=$LLMDBENCH_DEPLOY_METHODS)) \n \
              -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO) ] \n \
              -d/--deep [\"deep cleaning\"] (default=$LLMDBENCH_CONTROL_DEEP_CLEANING) ] \n \
              -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
              -m/--models [list the models to be deployed (default=$LLMDBENCH_DEPLOY_MODEL_LIST) ] \n \
              -t/--methods [list the methods employed to carry out the deployment (default=$LLMDBENCH_DEPLOY_METHODS) ] \n \
              -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE) ] \n \
              -h/--help (show this help)"
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
        -c=*|--scenario=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=$(echo $key | cut -d '=' -f 2)
        ;;
        -c|--scenario)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO="$2"
        shift
        ;;
        -t=*|--types=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS=$(echo $key | cut -d '=' -f 2)
        ;;
        -t|--types)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS="$2"
        shift
        ;;
        -d|--deep)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DEEP_CLEANING=1
        ;;
        -n|--dry-run)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DRY_RUN=1
        ;;
        -v|--verbose)
        export LLMDBENCH_CONTROL_VERBOSE=1
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

announce "🧹 Cleaning up namespace: $LLMDBENCH_CLUSTER_NAMESPACE"

# Special case: Helm release (if used)
hclist=$($LLMDBENCH_CONTROL_HCMD --namespace $LLMDBENCH_CLUSTER_NAMESPACE list --no-headers | grep vllm-p2p || true)
hclist=$(echo "${hclist}" | awk '{ print $1 }')
for hc in ${hclist}; do
  echo "🧽 Deleting Helm release \"${hc}\"..."
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_HCMD} uninstall ${hc} --namespace $LLMDBENCH_CLUSTER_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN}
done

if [[ $LLMDBENCH_CONTROL_DEEP_CLEANING -eq 0 ]]; then
  allres=$(${LLMDBENCH_CONTROL_KCMD} --namespace $LLMDBENCH_CLUSTER_NAMESPACE get ${LLMDBENCH_CONTROL_RESOURCE_LIST} -o name)
  tgtres=$(echo "$allres" | grep -Ev "configmap/kube-root-ca.crt|configmap/odh-trusted-ca-bundle|configmap/openshift-service-ca.crt|secret/${LLMDBENCH_VLLM_COMMON_PULL_SECRET_NAME}|secret/${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}")

  if [[ ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE} -eq 1 && ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_P2P_ACTIVE} -eq 0 ]]; then
    tgtres=$(echo "$tgtres" | grep standalone)
  fi

  if [[ ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE} -eq 0 && ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_P2P_ACTIVE} -eq 1 ]]; then
    tgtres=$(echo "$tgtres" | grep -E "p2p|inference-gateway|llm-route|base-model|endpoint-picker|inference-route|inference-gateway-secret|inference-gateway-params|inference-gateway")
  fi

  for delres in $tgtres; do
   llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete --namespace $LLMDBENCH_CLUSTER_NAMESPACE --ignore-not-found=true $delres" ${LLMDBENCH_CONTROL_DRY_RUN}
  done
else
# List of resource kinds to clean up
  RESOURCE_KINDS=(
    deployment
    service
    secret
    pvc
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
  )

# Delete each resource type (ignoring not found errors)
  for kind in "${RESOURCE_KINDS[@]}"; do
    announce "🗑️  Deleting all $kind in namespace $LLMDBENCH_CLUSTER_NAMESPACE..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} delete "$kind" --all -n "$LLMDBENCH_CLUSTER_NAMESPACE" --ignore-not-found=true || true" ${LLMDBENCH_CONTROL_DRY_RUN}
  done
fi

if [[ $LLMDBENCH_CONTROL_DEEP_CLEANING -eq 1 ]]; then
# Optional: delete cloned repos if they exist
  announce "🧼 Cleaning up local Git clones..."
  sleep 10
  llmdbench_execute_cmd "rm -rf ${LLMDBENCH_KVCM_DIR}/llm-d-kv-cache-manager ${LLMDBENCH_GAIE_DIR}/gateway-api-inference-extension ${LLMDBENCH_FMPERF_DIR}/fmperf" ${LLMDBENCH_CONTROL_DRY_RUN}
fi

announce "✅ Cleanup complete. Namespace '$LLMDBENCH_CLUSTER_NAMESPACE' is now cleared (except shared cluster-scoped resources like kgateway)."