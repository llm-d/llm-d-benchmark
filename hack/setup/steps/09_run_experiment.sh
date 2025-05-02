#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"

echo "Running experiment..."
cd fmperf
python3 examples/example_llm-d-lmbenchmark-openshift.py
cd ..
