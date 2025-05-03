#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "Running experiment..."
pushd ${LLMDBENCH_FMPERF_DIR}/fmperf &>/dev/null

# Hardcode Conda init from known working path
if [ -f "/opt/homebrew/anaconda3/etc/profile.d/conda.sh" ]; then
  source "/opt/homebrew/anaconda3/etc/profile.d/conda.sh"
  conda activate "$LLMDBENCH_CONDA_ENV_NAME"
else
  echo "❌ Could not find conda.sh. Please verify your Anaconda installation."
  exit 1
fi

# Confirm we're using the correct Python environment
echo "✅ Python: $(which python3)"
echo "✅ Env: $(conda info --envs | grep '*' || true)"
${LLMDBENCH_PCMD} -m pip show urllib3 >/dev/null 2>&1 || python3 -m pip install urllib3
${LLMDBENCH_PCMD} -m pip show kubernetes >/dev/null 2>&1 || python3 -m pip install kubernetes
${LLMDBENCH_PCMD} -m pip show pandas >/dev/null 2>&1 || python3 -m pip install pandas
pip install -e .

for experiment in ${LLMDBENCH_FMPERF_EXPERIMENT_LIST//,/ }; do
  if [ ! -f "$experiment" ]; then
    echo "❌  File '${experiment}' not found in branch '${LLMDBENCH_FMPERF_GIT_BRANCH}'. Stopping..."
    exit 1
  else
    ${LLMDBENCH_PCMD} ${experiment}
  fi
done
popd ${LLMDBENCH_FMPERF_DIR} &>/dev/null