#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Installing Anaconda..."
if ! command -v conda &>/dev/null; then
  brew install anaconda
  ANACONDA_PATH='export PATH="/opt/homebrew/anaconda3/bin:$PATH"'
  if ! grep -Fxq "$ANACONDA_PATH" ~/.zshrc; then
    echo "$ANACONDA_PATH" >> ~/.zshrc
    echo "✅ Anaconda path added to ~/.zshrc"
  else
    echo "ℹ️ Anaconda path already present in ~/.zshrc"
  fi
  source ~/.zshrc
fi
