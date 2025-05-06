#!/usr/bin/env bash

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_DIR=$(realpath $(pwd)/)

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

LLMDBENCH_STEPS_DIR="$LLMDBENCH_DIR/steps"

source ${LLMDBENCH_STEPS_DIR}/env.sh

export LLMDBENCH_DEEP_CLEANING=0
export LLMDBENCH_DRY_RUN=0

function show_usage {
    echo -e "Usage: $0 -t/--type [list of environment types targeted for cleaning (default=$LLMDBENCH_ENVIRONMENT_TYPES)) \n \
                              -d/--deep [\"deep cleaning\"] (default=$LLMDBENCH_DEEP_CLEANING) ] \n \
                              -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_DRY_RUN) ] \n \
                              -h/--help (show this help)"
}

while [[ $# -gt 0 ]]; do
    key="$1"

    case $key in
        -t=*|--type=*)
        export LLMDBENCH_ENVIRONMENT_TYPES=$(echo $key | cut -d '=' -f 2)
        ;;
        -t|--type)
        export LLMDBENCH_ENVIRONMENT_TYPES="$2"
        shift
        ;;
        -d|--deep)
        export LLMDBENCH_DEEP_CLEANING=1
        ;;
        -n|--dry-run)
        export LLMDBENCH_DRY_RUN=1
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

echo "🧹 Cleaning up namespace: $LLMDBENCH_OPENSHIFT_NAMESPACE"

# Special case: Helm release (if used)
hclist=$($LLMDBENCH_HCMD --namespace $LLMDBENCH_OPENSHIFT_NAMESPACE list --no-headers | grep vllm-p2p || true)
hclist=$(echo "${hclist}" | awk '{ print $1 }')
for hc in ${hclist}; do
  echo "🧽 Deleting Helm release \"${hc}\"..."
  llmdbench_execute_cmd "${LLMDBENCH_HCMD} uninstall ${hc} --namespace $LLMDBENCH_OPENSHIFT_NAMESPACE" ${LLMDBENCH_DRY_RUN}
done

if [[ $LLMDBENCH_DEEP_CLEANING -eq 0 ]]; then
  allres=$(${LLMDBENCH_KCMD} --namespace $LLMDBENCH_OPENSHIFT_NAMESPACE get deployment,service,httproute,gateway,gatewayparameters,inferencepool,inferencemodel,cm,ing,pod,secret -o name)
  tgtres=$(echo "$allres" | grep -Ev "configmap/kube-root-ca.crt|configmap/odh-trusted-ca-bundle|configmap/openshift-service-ca.crt")

  is_env_type_standalone=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep standalone || true)
  is_env_type_vllm=$(echo $LLMDBENCH_ENVIRONMENT_TYPES | grep vllm || true)

  if [[ ! -z ${is_env_type_standalone} && -z ${is_env_type_vllm} ]]; then
    tgtres=$(echo "$tgtres" | grep standalone)
  fi

  if [[ -z ${is_env_type_standalone} && ! -z ${is_env_type_vllm} ]]; then
    tgtres=$(echo "$tgtres" | grep -E "vllm|inference-gateway|llm-route")
  fi

  for delres in $tgtres; do
   llmdbench_execute_cmd "${LLMDBENCH_KCMD} delete --namespace $LLMDBENCH_OPENSHIFT_NAMESPACE $delres" ${LLMDBENCH_DRY_RUN}
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
    echo "🗑️  Deleting all $kind in namespace $LLMDBENCH_OPENSHIFT_NAMESPACE..."
    llmdbench_execute_cmd "${LLMDBENCH_KCMD} delete "$kind" --all -n "$LLMDBENCH_OPENSHIFT_NAMESPACE" --ignore-not-found=true || true" ${LLMDBENCH_DRY_RUN}
  done
fi

if [[ $LLMDBENCH_DEEP_CLEANING -eq 1 ]]; then
# Optional: delete cloned repos if they exist
  echo "🧼 Cleaning up local Git clones..."
  llmdbench_execute_cmd "rm -rf ${LLMDBENCH_KVCM_DIR}/llm-d-kv-cache-manager ${LLMDBENCH_GAIE_DIR}/gateway-api-inference-extension ${LLMDBENCH_FMPERF_DIR}/fmperf" ${LLMDBENCH_DRY_RUN}
fi

echo "✅ Cleanup complete. Namespace '$LLMDBENCH_OPENSHIFT_NAMESPACE' is now cleared (except shared cluster-scoped resources like kgateway)."
