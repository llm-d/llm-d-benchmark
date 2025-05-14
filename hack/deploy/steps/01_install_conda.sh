#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if ! conda -h &>/dev/null; then
  if [ $LLMDBENCH_CONTROL_DEPLOY_HOST_OS == "mac" ]; then
    announce "Installing Miniforge for macOS..."
    llmdbench_execute_cmd "brew install --cask miniforge" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  else
    # For Linux, you can use the official Miniforge installer script
    announce "Installing Miniforge for Linux..."
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

if ! grep -Fxq "$ANACONDA_PATH" ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc && ${LLMDBENCH_CONTROL_DRY_RUN} -eq 0 ; then
  announce "$ANACONDA_PATH" >> ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc
  announce "✅ Anaconda path added to ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc"
else
  announce "ℹ️ Anaconda path already present in ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc"
fi

# no need to source - we already export for current shell - next shell will naturally pick it up
# source ~/.${LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL}rc
