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

source ${LLMDBENCH_SETUP_DIR}/env.sh
export LLMDBENCH_CONTROL_WORK_DIR=$(mktemp -d -t ${LLMDBENCH_CONTROL_CLUSTER_NAME}-$(echo $0 | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_CONTROL_SCMD -e 's^.sh^^g' -e 's^./^^g')XXX)
prepare_work_dir
  cat << EOF > $LLMDBENCH_MAIN_DIR/workload/profiles/nop/unitest.yaml.in
param1: a
param2: REPLACE_ENV_LLMDBENCH_UNITEST_RENDER_PARAM_WITHOUT_DEFAULT                # comment
param3: REPLACE_ENV_LLMDBENCH_UNITEST_RENDER_PARAM_WITH_DEFAULT++++default=z      # another comment
param4:
  param4a: XYZ
  parambb: ABC
EOF
cat $LLMDBENCH_MAIN_DIR/workload/profiles/nop/unitest.yaml.in | yq .
echo "-----------"
export LLMDBENCH_UNITEST_RENDER_PARAM_WITHOUT_DEFAULT=b
echo "export LLMDBENCH_UNITEST_RENDER_PARAM_WITHOUT_DEFAULT=b"
render_workload_templates unitest
find ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/
cat ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/nop/unitest.yaml | yq .
echo "-----------"
export LLMDBENCH_UNITEST_RENDER_PARAM_WITH_DEFAULT=c
echo "export LLMDBENCH_UNITEST_RENDER_PARAM_WITH_DEFAULT=c"
render_workload_templates unitest
find ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/
cat ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/nop/unitest.yaml | yq .
echo "-----------"
cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/run_parameters.yaml
factors:
  - param1
  - param4a
levels:
  param1: "40,60"
  param4a: "80000,5000,1000"
treatments:
  - "40,8000"
  - "60,5000"
  - "60,1000"
EOF
rm ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/nop/unitest.yaml
generate_profile_parameter_treatments $LLMDBENCH_CONTROL_WORK_DIR/run_parameters.yaml nop
ls ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/nop/treatment_list
cat -n ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/nop/treatment_list/*
echo
echo
render_workload_templates unitest
find ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/
cat -n ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/nop/unitest*
rm -rf $LLMDBENCH_MAIN_DIR/workload/profiles/nop/unitest.yaml.in