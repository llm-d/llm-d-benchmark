#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

echo "Running experiment..."

    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/fmperf/test.py
import os
import urllib3

import kubernetes
from kubernetes import client, config
kubernetes.config.load_kube_config()
apiclient = client.ApiClient()
v1 = client.CoreV1Api()
print(v1.list_namespaced_pod(namespace="${LLMDBENCH_OPENSHIFT_NAMESPACE}"))
EOF
pushd ${LLMDBENCH_CONTROL_WORK_DIR} &>/dev/null

${LLMDBENCH_CONTROL_PCMD} fmperf/test.py

exit

pushd ${LLMDBENCH_FMPERF_DIR}/fmperf &>/dev/null

# Hardcode Conda init from known working path
if [ -f "/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh" ]; then
  llmdbench_execute_cmd "source \"/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}
  llmdbench_execute_cmd "conda activate \"$LLMDBENCH_FMPERF_CONDA_ENV_NAME\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_VERBOSE}
else
  echo "❌ Could not find conda.sh. Please verify your Anaconda installation."
  exit 1
fi

if [[ ${LLMDBENCH_CONTROL_DRY_RUN} -eq 0 ]]; then
# Confirm we're using the correct Python environment
  echo "✅ Python: $(which python3)"
  echo "✅ Env: $(conda info --envs | grep '*' || true)"
  ${LLMDBENCH_CONTROL_PCMD} -m pip show urllib3 >/dev/null 2>&1 || ${LLMDBENCH_CONTROL_PCMD} -m pip install urllib3
  ${LLMDBENCH_CONTROL_PCMD} -m pip show kubernetes >/dev/null 2>&1 || ${LLMDBENCH_CONTROL_PCMD} -m pip install kubernetes
  ${LLMDBENCH_CONTROL_PCMD} -m pip show pandas >/dev/null 2>&1 || ${LLMDBENCH_CONTROL_PCMD} -m pip install pandas
  pip install -e .

  for experiment in ${LLMDBENCH_FMPERF_EXPERIMENT_LIST//,/ }; do
    if [ ! -f "$experiment" ]; then
      echo "❌  File '${experiment}' not found in branch '${LLMDBENCH_FMPERF_GIT_BRANCH}'. Stopping..."
      exit 1
    else
      ${LLMDBENCH_CONTROL_PCMD} ${experiment}
    fi
  done
fi
popd ${LLMDBENCH_FMPERF_DIR} &>/dev/null
