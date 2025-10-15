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

export LLMDBENCH_CONTROL_WORK_DIR=${LLMDBENCH_CONTROL_WORK_DIR:-}
if [[ ! -z ${LLMDBENCH_CONTROL_WORK_DIR} ]]; then
  export LLMDBENCH_CONTROL_WORK_DIR_SET=1
fi
source ${LLMDBENCH_CONTROL_DIR}/env.sh

export LLMDBENCH_STEPS_DIR="$LLMDBENCH_CONTROL_DIR/steps"
export LLMDBENCH_CONTROL_DRY_RUN=${LLMDBENCH_CONTROL_DRY_RUN:-0}
export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=
export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=
LLMDBENCH_STEP_LIST=$(find $LLMDBENCH_STEPS_DIR -name "*.sh" -o -name "*.py" | $LLMDBENCH_CONTROL_SCMD -e "s^.sh^^g" -e "s^.py^^g" | grep -v 11_ | sort | rev | cut -d '/' -f 1 | rev | uniq)

function show_usage {
    echo -e "Usage: ${LLMDBENCH_CONTROL_CALLER} -s/--step [step list] (default=$(echo $LLMDBENCH_STEP_LIST | $LLMDBENCH_CONTROL_SCMD -e s^${LLMDBENCH_STEPS_DIR}/^^g -e 's/ /,/g') \n \
            -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO) ] \n \
            -m/--models [list the models to be stood up (default=$LLMDBENCH_DEPLOY_MODEL_LIST) ] \n \
            -p/--namespace [comma separated pair of values indicating where a stack will be stood up and where the benchmark will (later) be run (default=$LLMDBENCH_VLLM_COMMON_NAMESPACE,$LLMDBENCH_HARNESS_NAMESPACE)] \n \
            -t/--methods [list of standup methods (default=$LLMDBENCH_DEPLOY_METHODS, possible values \"standalone\" and \"modelservice\") ] \n \
            -a/--affinity [kubernetes node affinity] (default=$LLMDBENCH_VLLM_COMMON_AFFINITY) \n \
            -b/--annotations [kubernetes pod annotations] (default=$LLMDBENCH_VLLM_COMMON_ANNOTATIONS) \n \
            -r/--release [modelservice helm chart release name (default=$LLMDBENCH_VLLM_MODELSERVICE_RELEASE)] \n \
            -x/--dataset [url for dataset to be replayed (default=$LLMDBENCH_RUN_DATASET_URL)]
            -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
            -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE) ] \n \
            -h/--help (show this help)\n \

            * [step list] can take of form of comma-separated single/double digits (e.g. \"-s 0,1,5\") or ranges (e.g. \"-s 1-7\")"
}

while [[ $# -gt 0 ]]; do
    key="$1"

    case $key in
        -s=*|--step=*)
        export LLMDBENCH_CLIOVERRIDE_STEP_LIST=$(echo $key | cut -d '=' -f 2)
        ;;
        -s|--step)
        export LLMDBENCH_CLIOVERRIDE_STEP_LIST="$2"
        shift
        ;;
        -c=*|--scenario=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=$(echo $key | cut -d '=' -f 2)
        ;;
        -c|--scenario)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO="$2"
        shift
        ;;
        -m=*|--models=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_MODEL_LIST=$(echo $key | cut -d '=' -f 2)
        ;;
        -m|--models)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_MODEL_LIST="$2"
        shift
        ;;
        -p=*|--namespace=*)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE=$(echo $key | cut -d '=' -f 2 | cut -d ',' -f 1)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE=$(echo $key | cut -d '=' -f 2 | cut -d ',' -f 2)
        if [[ -z $LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE ]]; then
          export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE=$LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE
        fi
        ;;
        -p|--namespace)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE="$(echo $2 | cut -d ',' -f 1)"
        export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE="$(echo $2 | cut -d ',' -f 2)"
        if [[ -z $LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE ]]; then
          export LLMDBENCH_CLIOVERRIDE_HARNESS_NAMESPACE=$LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_NAMESPACE
        fi
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
        export LLMDBENCH_CLIOVERRIDE_VLLM_MODELSERVICE_RELEASE=$(echo $key | cut -d '=' -f 2)
        ;;
        -r|--release)
        export LLMDBENCH_CLIOVERRIDE_VLLM_MODELSERVICE_RELEASE="$2"
        shift
        ;;
        -a=*|--affinity=*)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_AFFINITY=$(echo $key | cut -d '=' -f 2)
        ;;
        -a|--affinity)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_AFFINITY="$2"
        shift
        ;;
        -x=*|--dataset=*)
        export LLMDBENCH_CLIOVERRIDE_RUN_DATASET_URL=$(echo $key | cut -d '=' -f 2)
        ;;
        -x|--dataset)
        export LLMDBENCH_CLIOVERRIDE_RUN_DATASET_URL="$2"
        shift
        ;;
        -b=*|--annotations=*)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_ANNOTATIONS=$(echo $key | cut -d '=' -f 2)
        ;;
        -b|--annotations)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_ANNOTATIONS="$2"
        shift
        ;;
        -n|--dry-run)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DRY_RUN=1
        ;;
        -v|--verbose)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_VERBOSE=1
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


_e=$(echo ${LLMDBENCH_STEP_LIST} | grep "[0-9]-[0-9]" | grep -v 11_ || true)
if [[ ! -z ${_e} ]]; then
  LLMDBENCH_STEP_LIST=$(eval echo $(echo {${LLMDBENCH_STEP_LIST}} | $LLMDBENCH_CONTROL_SCMD 's^-^..^g'))
fi
LLMDBENCH_STEP_LIST=$(echo $LLMDBENCH_STEP_LIST | $LLMDBENCH_CONTROL_SCMD 's^,^ ^g')

if [[ $LLMDBENCH_STEP_LIST == $(find $LLMDBENCH_STEPS_DIR -name "*.sh" -o -name "*.py" | $LLMDBENCH_CONTROL_SCMD -e "s^.sh^^g" -e "s^.py^^g" |  sort | rev | cut -d '/' -f 1 | rev | uniq | $LLMDBENCH_CONTROL_SCMD -e ':a;N;$!ba;s/\n/ /g' ) ]]; then
  export LLMDBENCH_CONTROL_STANDUP_ALL_STEPS=1
fi

extract_environment
sleep 5

for step in ${LLMDBENCH_STEP_LIST//,/ }; do
  if [[ ${#step} -lt 2 ]]
  then
    step=$(printf %02d $step)
  fi
  run_step "$step"
done

announce "ℹ️  The current work dir is \"${LLMDBENCH_CONTROL_WORK_DIR}\". Run \"export LLMDBENCH_CONTROL_WORK_DIR=$LLMDBENCH_CONTROL_WORK_DIR\" if you wish subsequent executions use the same diretory"
announce "✅ All steps complete."
