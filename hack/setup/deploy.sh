#!/usr/bin/env bash

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_DIR=$(realpath $(pwd)/)

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi


export LLMDBENCH_STEPS_DIR="$LLMDBENCH_DIR/steps"
export LLMDBENCH_DRY_RUN=${LLMDBENCH_DRY_RUN:-0}
LLMDBENCH_STEP_LIST=$LLMDBENCH_STEPS_DIR"/*.sh"

function show_usage {
    echo -e "Usage: $0 -s/--step [step list] (default=$LLMDBENCH_STEP_LIST) \n \
                                -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_DRY_RUN) ] \n \
                                -h/--help (show this help)"
}

while [[ $# -gt 0 ]]; do
    key="$1"

    case $key in
        -s=*|--step=*)
        export LLMDBENCH_STEP_LIST=$(echo $key | cut -d '=' -f 2)
        ;;
        -s|--step)
        export LLMDBENCH_STEP_LIST="$2"
        shift
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

run_step() {
  local script_name=$1
  local script_path=$(ls ${LLMDBENCH_STEPS_DIR}/${script_name}*)
  if [ -f $script_path ]; then
    local step_id=$(basename "$script_path")
    echo -e "\n=== Running step: $step_id ==="
    if [[ $LLMDBENCH_DRY_RUN -eq 1 ]]; then
      echo "[DRY RUN] $script_path"
    fi
    source $script_path
  fi
}

for step in ${LLMDBENCH_STEP_LIST//,/ }; do
  step=$(printf %02d $step)
  run_step "$step"
done

echo "✅ All steps complete."
