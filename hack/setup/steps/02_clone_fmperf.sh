#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Cloning and setting up fmperf..."
git clone "${GIT_REPO}" -b "${GIT_BRANCH}" || true
cd fmperf
conda create -y -n "$CONDA_ENV_NAME" python=3.11
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV_NAME"
pip install -r requirements.txt
pip install -e .
docker build -t fmperf .
mkdir -p requests && chmod o+w requests
cp .env.example .env
cd ..
