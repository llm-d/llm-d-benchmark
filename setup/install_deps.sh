#!/usr/bin/env bash

tools="gsed python3 oc helm helmfile kubectl kustomize rsync"
for tool in $tools; do
    if command -v $tool &> /dev/null; then
        echo "$tool already installed"
        continue
    fi
    echo "Installing $tool..."
    apt-get install -y $tool || brew install $tool || yum install -y $tool || echo "Could not install $tool"
done