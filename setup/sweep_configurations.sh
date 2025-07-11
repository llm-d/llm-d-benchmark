#!/usr/bin/env bash

export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=benchmark-hf-token
export LLMDBENCH_VLLM_DEPLOYER_RELEASE=benchmark-release
export LLMDBENCH_VLLM_COMMON_NAMESPACE=benchmark-test

# Base scenario file to use
base_scenario=ocp_H200_deployer_PD_base

# Decode and pod configurations, where each pair is "(number of replicas),(TP size)"
decode_conf_array=("1,4" "2,4" "4,4" "1,8" "2,8" "4,8")
prefill_conf_array=("1,1" "2,1" "4,1" "1,2" "2,2" "4,2")

if [[ -z "${LLMDBENCH_HF_TOKEN}" ]]; then
  echo "Must place HuggingFace token in environment variable: LLMDBENCH_HF_TOKEN"
  exit 1
fi

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi
export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/)
if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi
export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../)

erase_and_quit=0
while [[ $# -gt 0 ]]; do
  key="$1"

  case $key in
    -e|--erase) # Erase generated scenario files matching supplied base, then exit
    export erase_and_quit=1
    ;;
    *)
    echo "ERROR: invalid option \"$key\""
    exit 1
    ;;
    esac
    shift
done

# Ensure scenario name excludes suffix or path
base_scenario=$(echo "$base_scenario" | sed 's^.sh^^g' | rev | cut -d '/' -f 1 | rev)

if [ ! -e $LLMDBENCH_MAIN_DIR/scenarios/$base_scenario.sh ]; then
  echo "Could not find base scenario file: $LLMDBENCH_MAIN_DIR/scenarios/$base_scenario.sh"
  exit 1
fi

# Remove old scenario files matching base, to avoid running them
rm -f $LLMDBENCH_MAIN_DIR/scenarios/${base_scenario}__*
if [[ $erase_and_quit == 1 ]]; then
  echo "Erased generated scenario files"
  exit 0
fi

# Generate scenario files
for decode_conf in "${decode_conf_array[@]}"; do
  decode_replicas="${decode_conf%,*}" 
  decode_tp="${decode_conf#*,}"
  for prefill_conf in "${prefill_conf_array[@]}"; do
    prefill_replicas="${prefill_conf%,*}" 
    prefill_tp="${prefill_conf#*,}"

    scenario_suffix="__${prefill_replicas}P-TP${prefill_tp}_${decode_replicas}D-TP${decode_tp}"
    scenario_file="$LLMDBENCH_MAIN_DIR/scenarios/${base_scenario}${scenario_suffix}.sh"
    sed -e "s/__p_rep__/${prefill_replicas}/g" -e "s/__p_tp__/${prefill_tp}/g" -e "s/__d_rep__/${decode_replicas}/g" -e "s/__d_tp__/${decode_tp}/g" -e "s/__suffix__/${scenario_suffix}/g" $LLMDBENCH_MAIN_DIR/scenarios/$base_scenario.sh > $scenario_file
  done
done


# These are the configurations we will sweep over
scenarios=($(ls -d $LLMDBENCH_MAIN_DIR/scenarios/${base_scenario}__* ))
echo "Scenarios to sweep:"
printf "  %s\n" "${scenarios[@]}"

