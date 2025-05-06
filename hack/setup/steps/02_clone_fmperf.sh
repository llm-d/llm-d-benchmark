#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "Cloning and setting up fmperf..."
pushd ${LLMDBENCH_FMPERF_DIR} &>/dev/null
if [[ ! -d fmperf ]]; then
  llmdbench_execute_cmd "git clone \"${LLMDBENCH_FMPERF_GIT_REPO}\" -b \"${LLMDBENCH_FMPERF_GIT_BRANCH}\"" ${LLMDBENCH_DRY_RUN} 1
else
  pushd fmperf &>/dev/null
  llmdbench_execute_cmd "git checkout ${LLMDBENCH_FMPERF_GIT_BRANCH}; git pull" ${LLMDBENCH_DRY_RUN} 1
  popd &>/dev/null
fi
pushd fmperf &>/dev/null
git checkout ${LLMDBENCH_FMPERF_GIT_BRANCH}
is_ce=$(conda env list | grep $LLMDBENCH_CONDA_ENV_NAME || true)
is_ce=$(echo "$is_ce" | awk '{ print $1 }')
if [[ ! -z $is_ce ]]; then
  echo "❗ Conda environment \"${LLMDBENCH_CONDA_ENV_NAME}\" already set. Skipping install."
else
  conda create -y -n "$LLMDBENCH_CONDA_ENV_NAME" python=3.11
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "$LLMDBENCH_CONDA_ENV_NAME"
  pip install -r requirements.txt
  pip install -e .

  docker build -t fmperf .
  mkdir -p requests && chmod o+w requests
  cp .env.example .env
fi
popd &>/dev/null
popd &>/dev/null