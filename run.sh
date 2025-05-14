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

export LLMDBENCH_CONTROL_DIR=$(realpath $(pwd)/)'/hack/deploy'

if [ $0 != "-bash" ] ; then
    popd  > /dev/null 2>&1
fi

export LLMDBENCH_MAIN_DIR=$(realpath ${LLMDBENCH_CONTROL_DIR}/../../)

source ${LLMDBENCH_CONTROL_DIR}/env.sh

export LLMDBENCH_CONTROL_DRY_RUN=${LLMDBENCH_CONTROL_DRY_RUN:-0}
export LLMDBENCH_CONTROL_VERBOSE=${LLMDBENCH_CONTROL_VERBOSE:-0}
export LLMDBENCH_DEPLOY_SCENARIO=
export LLMDBENCH_FMPERF_EXPERIMENT_SKIP=0

function show_usage {
    echo -e "Usage: $(echo $0 | rev | cut -d '/' -f 1 | rev) -n/--dry-run [just print the command which would have been executed (default=$LLMDBENCH_CONTROL_DRY_RUN) ] \n \
             -c/--scenario [take environment variables from a scenario file (default=$LLMDBENCH_DEPLOY_SCENARIO) ] \n \
             -z/--skip [skip the execution of the experiment, and only collect data (default=$LLMDBENCH_FMPERF_EXPERIMENT_SKIP) ] \n \
             -v/--verbose [print the command being executed, and result (default=$LLMDBENCH_CONTROL_VERBOSE) ] \n \
             -h/--help (show this help)"
}

while [[ $# -gt 0 ]]; do
    key="$1"

    case $key in
        -c=*|--scenario=*)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO=$(echo $key | cut -d '=' -f 2)
        ;;
        -c|--scenario)
        export LLMDBENCH_CLIOVERRIDE_DEPLOY_SCENARIO="$2"
        shift
        ;;
        -n|--dry-run)
        export LLMDBENCH_CLIOVERRIDE_CONTROL_DRY_RUN=1
        ;;
        -z|--skip)
        export LLMDBENCH_CLIOVERRIDE_FMPERF_EXPERIMENT_SKIP=1
        ;;
        -v|--verbose)
        export LLMDBENCH_CLIOVERRIDE_VERBOSE=1
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

announce "Running experiment (harness=$LLMDBENCH_FMPERF_EXPERIMENT_HARNESS, profile=$LLMDBENCH_FMPERF_EXPERIMENT_PROFILE)..."

pushd ${LLMDBENCH_FMPERF_DIR}/fmperf &>/dev/null

# Hardcode Conda init from known working path
if [ -f "/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh" ]; then
  llmdbench_execute_cmd "source \"/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "conda activate \"$LLMDBENCH_FMPERF_CONDA_ENV_NAME\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
else
  echo "❌ Could not find conda.sh. Please verify your Anaconda installation."
  exit 1
fi

if [[ ${LLMDBENCH_CONTROL_DRY_RUN} -eq 0 ]]; then
# Confirm we're using the correct Python environment
  announce "✅ Python: $(which $LLMDBENCH_CONTROL_PCMD)"
  announce "✅ Env: $(conda info --envs | grep '*' || true)"
  ${LLMDBENCH_CONTROL_PCMD} -m pip show urllib3 >/dev/null 2>&1 || ${LLMDBENCH_CONTROL_PCMD} -m pip install urllib3
  ${LLMDBENCH_CONTROL_PCMD} -m pip show kubernetes >/dev/null 2>&1 || ${LLMDBENCH_CONTROL_PCMD} -m pip install kubernetes
  ${LLMDBENCH_CONTROL_PCMD} -m pip show pandas >/dev/null 2>&1 || ${LLMDBENCH_CONTROL_PCMD} -m pip install pandas
  pip install -e . >/dev/null 2>&1
fi

llmdbench_execute_cmd "cp -f ${LLMDBENCH_MAIN_DIR}/workload/harnesses/$LLMDBENCH_FMPERF_EXPERIMENT_HARNESS ${LLMDBENCH_CONTROL_WORK_DIR}/workload/harnesses/$LLMDBENCH_FMPERF_EXPERIMENT_HARNESS" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
cat ${LLMDBENCH_MAIN_DIR}/workload/profiles/$LLMDBENCH_FMPERF_EXPERIMENT_PROFILE | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_MODEL_NAME^${LLMDBENCH_MODEL2PARAM[${LLMDBENCH_DEPLOY_MODEL_LIST}:name]}^g" -e "s^REPLACE_IMAGE^$LLMDBENCH_FMPERF_CONTAINER_IMAGE^g" > ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$LLMDBENCH_FMPERF_EXPERIMENT_PROFILE

ecmd="${LLMDBENCH_CONTROL_PCMD} ${LLMDBENCH_CONTROL_WORK_DIR}/workload/harnesses/$LLMDBENCH_FMPERF_EXPERIMENT_HARNESS"
if [[ $LLMDBENCH_FMPERF_EXPERIMENT_SKIP -eq 0 ]]; then
  announce "Starting the actual execution ..."
  if [[ ${LLMDBENCH_CONTROL_DRY_RUN} -eq 0 ]]; then
    $ecmd
  else
    echo "---> would have executed the command \"$ecmd\""
  fi
else
  announce "Skipping experiment execution"
fi

announce "Collecting results ..."
llmdbench_execute_cmd "mv $(pwd)/pod_log_response.txt ${LLMDBENCH_CONTROL_WORK_DIR}/results/pod_log_response.txt" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

PN=$(echo $LLMDBENCH_EXPERIMENT_ID | $LLMDBENCH_CONTROL_SCMD 's^_^-^g' | tr '[:upper:]' '[:lower:]')

cat <<EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/run_access_to_pvc.yaml
apiVersion: v1
kind: Pod
metadata:
  name: $PN
  namespace: $LLMDBENCH_CLUSTER_NAMESPACE
spec:
  containers:
  - name: rsync
    image: busybox
    command: ["sleep", "infinity"]
    volumeMounts:
    - name: requests
      mountPath: /requests
  volumes:
  - name: requests
    persistentVolumeClaim:
      claimName: $LLMDBENCH_FMPERF_PVC_NAME
EOF

llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/run_access_to_pvc.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod/$PN" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} cp $PN:/requests/${LLMDBENCH_EXPERIMENT_ID}/ ${LLMDBENCH_CONTROL_WORK_DIR}/results/" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_CLUSTER_NAMESPACE} delete pod $PN" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
announce "Done"

popd ${LLMDBENCH_FMPERF_DIR} &>/dev/null
