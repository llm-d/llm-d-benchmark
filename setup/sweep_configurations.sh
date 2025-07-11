#!/usr/bin/env bash

export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=benchmark-hf-token
export LLMDBENCH_VLLM_DEPLOYER_RELEASE=benchmark-release
export LLMDBENCH_VLLM_COMMON_NAMESPACE=benchmark-test

# Base scenario file to use
base_scenario=ocp_H200_deployer_PD_base

# Decode and pod configurations, where each pair is TP size and number of replicas
decode_conf_array=("4,1" "4,2" "4,4" "8,1" "8,2" "8,4")
prefill_conf_array=("1,1" "1,2" "1,4" "2,1" "2,2" "2,4")

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


# Ensure scenario name excludes suffix or path
base_scenario=$(echo "$base_scenario" | sed 's^.sh^^g' | rev | cut -d '/' -f 1 | rev)


if [ ! -e $LLMDBENCH_MAIN_DIR/scenarios/$base_scenario.sh ]; then
  echo "Could not find base scenario file: $LLMDBENCH_MAIN_DIR/scenarios/$base_scenario.sh"
  exit 1
fi


# Remove old scenario files matching base, to avoid running them
rm -f $LLMDBENCH_MAIN_DIR/scenarios/${base_scenario}__*

# Generate scenario files
for decode_conf in "${decode_conf_array[@]}"; do
  decode_tp="${decode_conf%,*}" 
  decode_replicas="${decode_conf#*,}"
  for prefill_conf in "${prefill_conf_array[@]}"; do
    prefill_tp="${prefill_conf%,*}" 
    prefill_replicas="${prefill_conf#*,}"

    scenario_suffix="__${prefill_replicas}P-TP${prefill_tp}_${decode_replicas}D-TP${decode_tp}"
    scenario_file="$LLMDBENCH_MAIN_DIR/scenarios/${base_scenario}${scenario_suffix}.sh"
    sed -e "s/__p_rep__/${prefill_replicas}/g" -e "s/__p_tp__/${prefill_tp}/g" -e "s/__d_rep__/${decode_replicas}/g" -e "s/__d_tp__/${decode_tp}/g" $LLMDBENCH_MAIN_DIR/scenarios/$base_scenario.sh > $scenario_file
  done
done


# These are the configurations we will sweep over
scenarios=($(ls -d $LLMDBENCH_MAIN_DIR/scenarios/${base_scenario}__* ))
echo "Scenarios to sweep:"
printf "  %s\n" "${scenarios[@]}"

