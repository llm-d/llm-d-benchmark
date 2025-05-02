#!/usr/bin/env bash
# source "$(dirname "$0")/env.sh"

echo "Installing Anaconda..."
if ! command -v conda &>/dev/null; then
  # Check if the system is macOS
  if [ "$(uname)" == "Darwin" ]; then
    echo "Installing Miniforge for macOS..."
    brew install --cask miniforge
  else
    # For Linux, you can use the official Miniforge installer script
    echo "Installing Miniforge for Linux..."

    # Download and run the installer
    MINIFORGE_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname -m)-$(uname -s).sh"
    wget -qO - $MINIFORGE_URL | bash -b -P /opt/miniconda
  fi

  ANACONDA_PATH='export PATH="/opt/homebrew/bin/conda:$PATH"'
  if ! grep -Fxq "$ANACONDA_PATH" ~/.zshrc; then
    echo "$ANACONDA_PATH" >> ~/.zshrc
    echo "✅ Anaconda path added to ~/.zshrc"
  else
    echo "ℹ️ Anaconda path already present in ~/.zshrc"
  fi
  source ~/.zshrc
fi
