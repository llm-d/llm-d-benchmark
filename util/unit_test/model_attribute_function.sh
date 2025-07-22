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

source ${LLMDBENCH_CONTROL_DIR}/env.sh

model_list="meta-llama/Llama-3.2-3B-Instruct RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic meta-llama/Llama-4-Scout-17B-16E-Instruct Qwen/Qwen1.5-MoE-A2.7B-Chat ibm-granite/granite-speech-3.3-8b ibm-granite/granite-vision-3.3-2b facebook/opt-125m"
for i in $model_list
do
  echo "-----------"
  for j in model modelcomponents provider type parameters majorversion kind label folder as_label
  do
    echo "$j : $(model_attribute $i $j)"
  done
  echo "-----------"
done

for method in modelservice standalone
do
  for model in $model_list
  do
    echo "$(echo ${method} | $LLMDBENCH_CONTROL_SCMD 's^modelservice^llm-d^g')-$(model_attribute $model parameters)-$(model_attribute $model type)"
  done
done
