#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Installing Anaconda..."
if ! command -v conda &>/dev/null; then
  brew install anaconda
  echo 'export PATH="/opt/homebrew/anaconda3/bin:$PATH"' >> ~/.zshrc
  source ~/.zshrc
fi
