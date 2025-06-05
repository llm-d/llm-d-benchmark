#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY -eq 0 ]]; then
  announce "⏭️ Environment variable \"LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY\" is set to 0, skipping local setup of conda environment"
  if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
  then
      exit 0
  else
      return 0
  fi
fi

if ! conda -h &>/dev/null; then
  if [ $LLMDBENCH_CONTROL_DEPLOY_HOST_OS == "mac" ]; then
    announce "🛠️ Installing Miniforge for macOS..."
    llmdbench_execute_cmd "brew install --cask miniforge" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  else
    # For Linux, you can use the official Miniforge installer script
    announce "🛠️ Installing Miniforge for Linux..."
    # Download and run the installer
    MINIFORGE_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname -s)-$(uname -m).sh"
   llmdbench_execute_cmd " wget -qO - $MINIFORGE_URL | bash -b -P /opt/miniconda" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  fi
fi

if [ $LLMDBENCH_CONTROL_DEPLOY_HOST_OS == "mac" ]; then
  ANACONDA_PATH='export PATH="/opt/homebrew/bin/conda:$PATH"'
else
  ANACONDA_PATH='export PATH="/opt/miniconda/bin/conda:$PATH"'
fi

if ! grep -Fxq "$ANACONDA_PATH" ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc && [[ "${LLMDBENCH_CONTROL_DRY_RUN}" -eq 0 ]]; then
  announce "$ANACONDA_PATH" >> ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc
  announce "✅ Anaconda path added to ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc"
else
  announce "⏭️  Anaconda path already present in ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc"
fi

if [ "$LLMDBENCH_CONTROL_DEPLOY_HOST_OS" = "mac" ] && [ -f "/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh" ]; then
  llmdbench_execute_cmd "source \"/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
elif [ "$LLMDBENCH_CONTROL_DEPLOY_HOST_OS" = "linux" ] && [ -f "/opt/miniconda/etc/profile.d/conda.sh" ]; then
  llmdbench_execute_cmd "source \"/opt/miniconda/etc/profile.d/conda.sh\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
else
  echo "❌ Could not find conda.sh for $LLMDBENCH_CONTROL_DEPLOY_HOST_OS. Please verify your Anaconda installation."
  if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
  then
      exit 1
  else
      return 1
  fi
fi

has_conda_env=$(conda env list | grep $LLMDBENCH_HARNESS_CONDA_ENV_NAME || true)
if [[ ! -z ${has_conda_env} ]]; then
  announce "⏭️  Conda environment \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\" already created, skipping installtion"
else
  announce "📜 Configuring conda environment \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\"..."
  llmdbench_execute_cmd "conda create --name \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\" -y" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
#  llmdbench_execute_cmd "conda init \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "conda activate \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  if [[ ${LLMDBENCH_CONTROL_DRY_RUN} -eq 0 ]]; then
    announce "ℹ️  Python: $(which $LLMDBENCH_CONTROL_PCMD)"
    announce "ℹ️  Env: $(conda info --envs | grep '*' || true)"
    ${LLMDBENCH_CONTROL_PCMD} -m pip install -r ${LLMDBENCH_MAIN_DIR}/build/requirements.txt
  fi
fi
announce "✅ Conda environment \"$LLMDBENCH_HARNESS_CONDA_ENV_NAME\" configured"
