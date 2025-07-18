#!/usr/bin/env bash

# common deps 
tools="gsed python3 oc helm helmfile kubectl kustomize rsync"

# get package manager
if command -v apt &> /dev/null; then
    PKG_MGR="apt install -y"
elif command -v apt-get &> /dev/null; then
    PKG_MGR="apt-get install -y"
elif command -v brew &> /dev/null; then
    PKG_MGR="brew install"
elif command -v yum &> /dev/null; then
    PKG_MGR="yum install -y"
elif command -v dnf &> /dev/null; then
    PKG_MGR="dnf install -y"
else
    echo "No supported package manager found (apt, apt-get, brew, yum, dnf)"
    exit 1
fi


for tool in $tools; do
    if command -v $tool &> /dev/null; then
        echo "$tool already installed"
        continue
    fi
    echo "Installing $tool..."
    $PKG_MGR $tool || echo "Could not install $tool"
done