#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Running experiment..."
cd fmperf

# Hardcode Conda init from known working path
if [ -f "/opt/homebrew/anaconda3/etc/profile.d/conda.sh" ]; then
  source "/opt/homebrew/anaconda3/etc/profile.d/conda.sh"
  conda activate "$CONDA_ENV_NAME"
else
  echo "❌ Could not find conda.sh. Please verify your Anaconda installation."
  exit 1
fi

# Confirm we're using the correct Python environment
echo "✅ Python: $(which python3)"
echo "✅ Env: $(conda info --envs | grep '*' || true)"
python3 -m pip show urllib3 >/dev/null 2>&1 || python3 -m pip install urllib3
python3 -m pip show kubernetes >/dev/null 2>&1 || python3 -m pip install kubernetes
python3 -m pip show pandas >/dev/null 2>&1 || python3 -m pip install pandas
pip install -e .
if [ ! -f "$FMPERF_EXAMPLE" ]; then
  echo "❌  File '${FMPERF_EXAMPLE}' not found in branch '${GIT_BRANCH}'. Stopping..."
  exit 1
else
  python3 ${FMPERF_EXAMPLE}
fi
cd ..
