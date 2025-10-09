#!/usr/bin/env bash

#set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/)

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../)
export LLMDBENCH_CONTROL_CALLER=$(echo $0 | rev | cut -d '/' -f 1 | rev)

if [[ ! -z ${LLMDBENCH_CONTROL_WORK_DIR} ]]; then
  export LLMDBENCH_CONTROL_WORK_DIR_SET=1
fi

source ${LLMDBENCH_CONTROL_DIR}/env.sh

export LLMDBENCH_STEPS_DIR="$LLMDBENCH_CONTROL_DIR/steps"
export LLMDBENCH_CONTROL_DEEP_CLEANING=${LLMDBENCH_CONTROL_DEEP_CLEANING:-0}
export LLMDBENCH_CONTROL_DRY_RUN=${LLMDBENCH_CONTROL_DRY_RUN:-0}
export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=
export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=
export LLMDBENCH_HARNESS_SKIP_RUN=0
export LLMDBENCH_HARNESS_DEBUG=0

LLMDBENCH_STEP_LIST=$(find $LLMDBENCH_STEPS_DIR -name "*.sh" | grep -v 11_ | sort | rev | cut -d '/' -f 1 | rev)

function show_usage {
    echo -e "Usage: ${LLMDBENCH_CONTROL_CALLER} -s/--step [step list] (default=$(echo $LLMDBENCH_STEP_LIST | $LLMDBENCH_CONTROL_SCMD -e s^${LLMDBENCH_STEPS_DIR}/^^g -e 's/ /,/g') \n \
            -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO) ] \n \
            -m/--models [list the models to be deployed (default=$LLMDBENCH_DEPLOY_MODEL_LIST) ] \n \
            -p/--namespace [comma separated pair of values indicating where a stack will be stood up and where the benchmark will be run (default=$LLMDBENCH_VLLM_COMMON_NAMESPACE,$LLMDBENCH_HARNESS_NAMESPACE)] \n \
            -t/--methods [list the methods employed to carry out the deployment (default=$LLMDBENCH_DEPLOY_METHODS, possible values \"standalone\" and \"modelservice\") ] \n \
            -a/--affinity [kubernetes node affinity] (default=$LLMDBENCH_VLLM_COMMON_AFFINITY) \n \
            -l/--harness [harness used to generate load (default=$LLMDBENCH_HARNESS_NAME, possible values $(get_harness_list)] \n \
            -w/--workload [workload to be used by the harness (default=$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE, possible values (check \"workload/profiles\" dir)] \n \
            -k/--pvc [name of the PVC used to store the results (default=$LLMDBENCH_HARNESS_PVC_NAME)] \n \
            -e/--experiments [path of yaml file containing a list of factors and levels for an experiment, useful for parameter sweeping (default=$LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS)] \n \
            -o/--overrides [comma-separated list of workload profile parameters to be overriden (default=$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES)] \n \
            -z/--skip [skip the execution of the experiment, and only collect data (default=$LLMDBENCH_HARNESS_SKIP_RUN)] \n \
            --wait [time to wait until the benchmark run is complete (default=$LLMDBENCH_HARNESS_WAIT_TIMEOUT, value \"0\" means "do not wait\""] \n \
            --debug [execute harness in \"debug-mode\" (default=$LLMDBENCH_HARNESS_DEBUG)] \n \
            -b/--annotations [kubernetes pod annotations] (default=$LLMDBENCH_VLLM_COMMON_ANNOTATIONS) \n \
            -r/--release [modelservice helm chart release name (default=$LLMDBENCH_VLLM_MODELSERVICE_RELEASE)] \n \
            -x/--dataset [url for dataset to be replayed (default=$LLMDBENCH_RUN_DATASET_URL)]
            -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
            -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE) ] \n \
            --deep [\"deep cleaning\"] (default=$LLMDBENCH_CONTROL_DEEP_CLEANING) ] \n \
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
        --wait=*)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_WAIT_TIMEOUT=$(echo $key | cut -d '=' -f 2)
        ;;
        --wait)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_WAIT_TIMEOUT="$2"
        shift
        ;;
        -l=*|--harness=*)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_NAME=$(echo $key | cut -d '=' -f 2)
        ;;
        -l|--harness)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_NAME="$2"
        shift
        ;;
        -k=*|--pvc=*)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_PVC_NAME=$(echo $key | cut -d '=' -f 2)
        ;;
        -k|--pvc)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_PVC_NAME="$2"
        shift
        ;;
        -w=*|--workload=*)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE=$(echo $key | cut -d '=' -f 2)
        ;;
        -w|--workload)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE="$2"
        shift
        ;;
        -e=*|--experiment=*)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_TREATMENTS=$(echo $key | cut -d '=' -f 2)
        ;;
        -e|--experiment)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_TREATMENTS="$2"
        shift
        ;;
        -o=*|--overrides=*)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE_OVERRIDES=$(echo $key | cut -d '=' -f 2)
        ;;
        -o|--overrides)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_EXPERIMENT_PROFILE_OVERRIDES="$2"
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
        -b=*|--annotations=*)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_ANNOTATIONS=$(echo $key | cut -d '=' -f 2)
        ;;
        -b|--annotations)
        export LLMDBENCH_CLIOVERRIDE_VLLM_COMMON_ANNOTATIONS="$2"
        shift
        ;;
        -z|--skip)
        export LLMDBENCH_CLIOVERRIDE_HARNESS_SKIP_RUN=1
        ;;
        -n|--dry-run)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DRY_RUN=1
        ;;
        --deep)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DEEP_CLEANING=1
        ;;
        --debug)
        export LLMDBENCH_HARNESS_DEBUG=1
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

sweeptmpdir=$(mktemp -d -t sweepXXX)

generate_standup_parameter_scenarios $sweeptmpdir $LLMDBENCH_SCENARIO_FULL_PATH $LLMDBENCH_HARNESS_EXPERIMENT_TREATMENTS
announce "ℹ️ A list of tretaments for standup paramaters was generated at \"${sweeptmpdir}\""
sleep 5

for scenario in $(ls $sweeptmpdir/setup/treatment_list/); do
  export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=$sweeptmpdir/setup/treatment_list/$scenario
  sid=$($LLMDBENCH_CONTROL_SCMD -e 's/[^[:alnum:]][^[:alnum:]]*/_/g' <<<"${scenario%.sh}")  # remove non alphanumeric and .sh
  sid=${sid#treatment_}
  export LLMDBENCH_RUN_EXPERIMENT_ID=$(date +%s)-${sid}

  backup_work_dir auto 1

  $LLMDBENCH_MAIN_DIR/setup/standup.sh
  ec=$?
  if [[ $ec -ne 0 ]]; then
    backup_work_dir $sid 1
    exit $ec
  fi
  rsync -az --inplace $sweeptmpdir/setup/treatment_list/ $LLMDBENCH_CONTROL_WORK_DIR/setup/treatment_list/
  echo
  echo
  echo
  echo
  $LLMDBENCH_MAIN_DIR/setup/run.sh
  ec=$?
  if [[ $ec -ne 0 ]]; then
    backup_work_dir $sid 1
    exit $ec
  fi
  echo
  echo
  echo
  echo
  if [[ $LLMDBENCH_HARNESS_DEBUG -eq 1 ]]; then
    announce "⏭️  Option \"--debug\" or environment variable \"LLMDBENCH_HARNESS_DEBUG\" was set to \"1\". Will not execute teardown"
    exit 0
  fi
  $LLMDBENCH_MAIN_DIR/setup/teardown.sh
  ec=$?
  if [[ $ec -ne 0 ]]; then
    backup_work_dir $sid 1
    exit $ec
  fi
  backup_work_dir $sid 1
done
