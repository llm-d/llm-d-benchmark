#!/usr/bin/env bash
source ${LLMDBENCH_STEPS_DIR}/env.sh

if ! conda -h &>/dev/null; then
  if [ $LLMDBENCH_HOST_OS == "mac" ]; then
    echo "Installing Miniforge for macOS..."
    llmdbench_execute_cmd "brew install --cask miniforge" ${LLMDBENCH_DRY_RUN}
  else
    # For Linux, you can use the official Miniforge installer script
    echo "Installing Miniforge for Linux..."
    # Download and run the installer
    MINIFORGE_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname -s)-$(uname -m).sh"
    wget -qO - $MINIFORGE_URL | bash -b -P /opt/miniconda
  fi
fi

if [ $LLMDBENCH_HOST_OS == "mac" ]; then
  ANACONDA_PATH='export PATH="/opt/homebrew/bin/conda:$PATH"'
else
  ANACONDA_PATH='export PATH="/opt/miniconda/bin/conda:$PATH"'
fi

if ! grep -Fxq "$ANACONDA_PATH" ~/.${LLMDBENCH_HOST_SHELL}rc && ${LLMDBENCH_DRY_RUN} -eq 0 ; then
  echo "$ANACONDA_PATH" >> ~/.${LLMDBENCH_HOST_SHELL}rc
  echo "✅ Anaconda path added to ~/.${LLMDBENCH_HOST_SHELL}rc"
else
  echo "ℹ️ Anaconda path already present in ~/.${LLMDBENCH_HOST_SHELL}rc"
fi

# no need to source - we already export for current shell - next shell will naturally pick it up
# source ~/.${LLMDBENCH_HOST_SHELL}rc
