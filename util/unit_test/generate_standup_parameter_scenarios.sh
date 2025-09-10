#!/usr/bin/env bash

# Copyright 2025 The llm-d Authors.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

if [[ $0 != "-bash" ]]; then
    pushd `dirname "$(realpath $0)"` > /dev/null 2>&1
fi

export LLMDBENCH_SETUP_DIR=$(realpath $(pwd)/../../setup)
export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_SETUP_DIR}/../)
export LLMDBENCH_CONTROL_CLUSTER_NAME=unit-test

export LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES=
source ${LLMDBENCH_SETUP_DIR}/env.sh
export LLMDBENCH_CONTROL_WORK_DIR=$(mktemp -d -t ${LLMDBENCH_CONTROL_CLUSTER_NAME}-$(echo $0 | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_CONTROL_SCMD -e 's^.sh^^g' -e 's^./^^g')XXX)
prepare_work_dir
cat << EOF > $LLMDBENCH_MAIN_DIR/scenarios/unit_test.sh
export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3

export LLMDBENCH_VLLM_COMMON_CPU_NR=32
export LLMDBENCH_VLLM_COMMON_CPU_MEM=128Gi
export LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN=32768
export LLMDBENCH_VLLM_COMMON_BLOCK_SIZE=128

export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS="[--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM____--disable-log-requests____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE]"

export LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM=1
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS="[--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM____--disable-log-requests____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--block-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE]"

export LLMDBENCH_CONTROL_WAIT_TIMEOUT=900000
export LLMDBENCH_HARNESS_WAIT_TIMEOUT=900000

export LLMDBENCH_CONTROL_WORK_DIR=~/benchmark_REPLACE_TREATMENT_NR
EOF

cat $LLMDBENCH_MAIN_DIR/scenarios/unit_test.sh
cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup_parameters.yaml
setup:
  factors:
    - LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS
    - LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM
    - LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS
    - LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM
  levels:
    LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS: "2,4,6,8"
    LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM: "1,2"
    LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS: "1,2"
    LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM: "4,8"
  treatments:
    - "6,2,1,4"
    - "4,2,1,8"
    - "8,1,1,8"
    - "4,2,2,4"
    - "4,2,4,2"
    - "2,2,4,4"
EOF
cat $LLMDBENCH_CONTROL_WORK_DIR/setup_parameters.yaml | yq -r .
echo
generate_standup_parameter_scenarios ${LLMDBENCH_CONTROL_WORK_DIR} $LLMDBENCH_MAIN_DIR/scenarios/unit_test.sh $LLMDBENCH_CONTROL_WORK_DIR/setup_parameters.yaml
ls -la ${LLMDBENCH_CONTROL_WORK_DIR}/setup/treatment_list
echo
for tf in $(ls ${LLMDBENCH_CONTROL_WORK_DIR}/setup/treatment_list*); do
  cat -n ${LLMDBENCH_CONTROL_WORK_DIR}/setup/treatment_list/${tf}
  echo
done
echo "------------------------------------------------------------------------------------------------------------------------------------"
echo
echo
rm -rf ${LLMDBENCH_CONTROL_WORK_DIR}/setup/treatment_list/
cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup_parameters.yaml
EOF
cat $LLMDBENCH_CONTROL_WORK_DIR/setup_parameters.yaml | yq -r .
echo
generate_standup_parameter_scenarios ${LLMDBENCH_CONTROL_WORK_DIR} $LLMDBENCH_MAIN_DIR/scenarios/unit_test.sh $LLMDBENCH_CONTROL_WORK_DIR/setup_parameters.yaml
ls -la ${LLMDBENCH_CONTROL_WORK_DIR}/setup/treatment_list
echo
for tf in $(ls ${LLMDBENCH_CONTROL_WORK_DIR}/setup/treatment_list*); do
  cat -n ${LLMDBENCH_CONTROL_WORK_DIR}/setup/treatment_list/${tf}
  echo
done
rm -rf $LLMDBENCH_MAIN_DIR/scenarios/unit_test.sh