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

export LLMDBENCH_STEPS_DIR="$LLMDBENCH_CONTROL_DIR/steps"
export LLMDBENCH_CONTROL_DRY_RUN=${LLMDBENCH_CONTROL_DRY_RUN:-0}
export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=
LLMDBENCH_STEP_LIST=$(find $LLMDBENCH_STEPS_DIR -name "*.sh" | grep -v 11_ | sort | rev | cut -d '/' -f 1 | rev)

function show_usage {
    echo -e "Usage: $(echo $0 | rev | cut -d '/' -f 1 | rev) -s/--step [step list] (default=$(echo $LLMDBENCH_STEP_LIST | $LLMDBENCH_CONTROL_SCMD -e s^${LLMDBENCH_STEPS_DIR}/^^g -e 's/ /,/g') \n \
            -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO) ] \n \
            -m/--models [list the models to be deployed (default=$LLMDBENCH_DEPLOY_MODEL_LIST) ] \n \
            -t/--methods [list the methods employed to carry out the deployment (default=$LLMDBENCH_DEPLOY_METHODS) ] \n \
            -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
            -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE) ] \n \
            -h/--help (show this help)"
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
        -t=*|--methods=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS=$(echo $key | cut -d '=' -f 2)
        ;;
        -t|--methods)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_METHODS="$2"
        shift
        ;;
        -n|--dry-run)
        export LLMDBENCH_CONTROL_DRY_RUN=1
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

run_step() {
  local script_name=$1

  if [[ -f $script_name ]]; then
    local script_path=$script_name
  else
    local script_path=$(ls ${LLMDBENCH_STEPS_DIR}/${script_name}*)
  fi
  if [ -f $script_path ]; then
    local step_id=$(basename "$script_path")
    local step_nr=$(echo $step_id | cut -d '_' -f 1)
    export LLMDBENCH_CURRENT_STEP=${step_nr}
    announce "=== Running step: $step_id ==="
    if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
      echo -e "[DRY RUN] $script_path\n"
    fi
    source $script_path
    echo
  else
    announce "ERROR: unable to run step \"${script_name}\""
  fi
}

_e=$(echo ${LLMDBENCH_STEP_LIST} | grep "[0-9]-[0-9]" | grep -v 11_ || true)
if [[ ! -z ${_e} ]]; then
  LLMDBENCH_STEP_LIST=$(eval echo $(echo {${LLMDBENCH_STEP_LIST}} | $LLMDBENCH_CONTROL_SCMD 's^-^..^g'))
fi
LLMDBENCH_STEP_LIST=$(echo $LLMDBENCH_STEP_LIST | $LLMDBENCH_CONTROL_SCMD 's^,^ ^g')

for step in ${LLMDBENCH_STEP_LIST//,/ }; do
  if [[ ${#step} -lt 2 ]]
  then
    step=$(printf %02d $step)
  fi
  run_step "$step"
done

announce "✅ All steps complete."