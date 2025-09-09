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

export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/../../setup)
export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../)
export LLMDBENCH_CONTROL_CLUSTER_NAME=unit-test

source ${LLMDBENCH_CONTROL_DIR}/env.sh
export LLMDBENCH_CONTROL_WORK_DIR=${LLMDBENCH_CONTROL_WORK_DIR:-$(mktemp -d -t ${LLMDBENCH_CONTROL_CLUSTER_NAME}-$(echo $0 | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_CONTROL_SCMD -e 's^.sh^^g' -e 's^./^^g')XXX)}
prepare_work_dir
export LLMDBENCH_DEPLOY_CURRENT_MODEL=$(model_attribute "meta-llama/Llama-3.2-3B-Instruct" model)
export LLMDBENCH_VLLM_STANDALONE_ARGS="REPLACE_ENV_LLMDBENCH_VLLM_STANDALONE_PREPROCESS____;____vllm____serve____REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL____--enable-sleep-mode____--load-format____REPLACE_ENV_LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT____--port____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT____--max-model-len____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN____--disable-log-requests____--gpu-memory-utilization____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL____--tensor-parallel-size____REPLACE_ENV_LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM"
export LLMDBENCH_CURRENT_STEP=06
export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE=1
export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE=0
export LLMDBENCH_VLLM_COMMON_ANNOTATIONS="deployed-by:$(id -un),modelservice:llm-d-benchmark,k8s.v1.cni.cncf.io/networks:multi-nic-compute"
cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/${LLMDBENCH_CURRENT_STEP}_a_deployment.yaml
      annotations:
        $(add_annotations)
    spec:
EOF
cat $LLMDBENCH_CONTROL_WORK_DIR/${LLMDBENCH_CURRENT_STEP}_a_deployment.yaml
echo "-----------"
echo
echo
export LLMDBENCH_CURRENT_STEP=08
export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE=1
export LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE=0
export LLMDBENCH_VLLM_COMMON_ANNOTATIONS="deployed-by:$(id -un),modelservice:llm-d-benchmark,k8s.v1.cni.cncf.io/networks:multi-nic-compute"
cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/${LLMDBENCH_CURRENT_STEP}_values.yaml
  annotations:
      $(add_annotations)
  containers:
EOF
cat $LLMDBENCH_CONTROL_WORK_DIR/${LLMDBENCH_CURRENT_STEP}_values.yaml
