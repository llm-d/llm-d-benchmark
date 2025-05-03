#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

echo "Cloning and setting up fmperf..."
pushd ${LLMDBENCH_FMPERF_DIR} &>/dev/null
if [[ ! -d fmperf ]]; then
  git clone "${LLMDBENCH_FMPERF_GIT_REPO}" -b "${LLMDBENCH_FMPERF_GIT_BRANCH}"
fi
pushd fmperf &>/dev/null
git checkout ${LLMDBENCH_FMPERF_GIT_BRANCH}
conda create -y -n "$LLMDBENCH_CONDA_ENV_NAME" python=3.11
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$LLMDBENCH_CONDA_ENV_NAME"
pip install -r requirements.txt
pip install -e .

docker build -t fmperf .
mkdir -p requests && chmod o+w requests
cp .env.example .env
popd &>/dev/null
popd &>/dev/null