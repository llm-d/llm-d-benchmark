#!/usr/bin/env bash

# This script takes a base scenario and a list of prefill and decode
# pod configurations (number of replicas and TP size), and for each combination
# of configurations will perform "standup" (create an instance of llm-d serving
# the model of interest in the desired configuration), "run" benchmarking, and
# "teardown" of llm-d.
#
# In order to pull models from Hugging Face, before executing this script you
# must export the environment variable LLMDBENCH_HF_TOKEN with your
# Hugging Face token
#   export LLMDBENCH_HF_TOKEN=my_secret_token
#
# This script will first generate a set of scenarios from a base scenario.
# Base scenarios are located in the scenarios/ directory of this repository,
# and end with the suffix "_base.sh". These base scenarios contain placeholder
# strings for number of prefill and decode replicas, and tensor parallel size.
# The generated scenarios will match the base scenario name, and have a suffix
# specifying the configuration for prefill and decode.

# These generated scenario files will be deleted and regenerated when this
# script is executed, so should not be edited by hand. To delete these files
# without performing any other operations, use the "--erase" flag.
#
# To generate these files for inspection without performing other operations,
# supply the "--generate" flag.

################################################################################
# User variables
################################################################################

# Base scenario file to use
base_scenario=ocp_H200_deployer_PD_base

# Decode and pod configurations, where each pair is "(number of replicas),(TP size)"
decode_conf_array=("1,4" "2,4" "4,4" "1,8" "2,8" "4,8")
prefill_conf_array=("1,1" "2,1" "4,1" "1,2" "2,2" "4,2")

export LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME=benchmark-hf-token
export LLMDBENCH_VLLM_DEPLOYER_RELEASE=benchmark-release
export LLMDBENCH_VLLM_COMMON_NAMESPACE=benchmark-test

################################################################################
# Main script
################################################################################

if [[ -z "${LLMDBENCH_HF_TOKEN}" ]]; then
  echo "Must place Hugging Face token in environment variable: LLMDBENCH_HF_TOKEN"
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
gen_and_quit=0
while [[ $# -gt 0 ]]; do
  key="$1"

  case $key in
    -e|--erase) # Erase generated scenario files matching supplied base, then exit
    export erase_and_quit=1
    ;;
    -g|--generate) # Generate scenario files matching supplied base, then exit
    export gen_and_quit=1
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

if [[ $gen_and_quit == 1 ]]; then
  echo "Generated scenario files"
  exit 0
fi

# Hack to set up deployer that supports heterogeneous P/D configurations
if [[ ! -d /tmp/llm-d-deployer ]]; then
  echo "Creating deployer in /tmp"
  pushd . &>/dev/null
  cd /tmp
  # Check out main branch of llm-d-deployer
  git clone https://github.com/llm-d/llm-d-deployer.git -b main
  cd llm-d-deployer
  #git checkout -q a51e9cae966d86c4f5a18e6111eaf33768581262
  # Switch to draft PR that includes changes needed for heterogeneous P/D
  git fetch origin pull/368/head:pr368 -q
  git switch pr368 -q
  git checkout -q 155838fc884186641615cd8056511bc0847f3b66
  # Merge in necessary updates from main
  git merge main -m "Merge main" -q
  # Apply patch to allow Kubernetes v1.28.0
  patch -p1 < ${LLMDBENCH_MAIN_DIR}/util/patches/llm-d-deployer.patch
  popd . &>/dev/null
else
  echo "/tmp/llm-d-deployer repository exists, ensure it includes appropriate modifications!"
fi

# These are the configurations we will sweep over
scenarios=($(ls -d $LLMDBENCH_MAIN_DIR/scenarios/${base_scenario}__* ))
echo "Scenarios to sweep:"
printf "  %s\n" "${scenarios[@]}"

for ss in "${scenarios[@]}"; do
  echo "**** Standing up scenario $ss****"
  #$LLMDBENCH_MAIN_DIR/standup.sh -c $ss
  echo "**** Running benchmarks for scenario $ss****"
  # TODO replace this with inner loop for concurrencies
  #$LLMDBENCH_MAIN_DIR/run.sh -c $ss
  echo "**** Tearing down scenario $ss****"
  #$LLMDBENCH_MAIN_DIR/teardown.sh -c $ss
done