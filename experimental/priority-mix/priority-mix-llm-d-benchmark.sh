#!/usr/bin/env bash

set -euo pipefail

python3 "${LLMDBENCH_RUN_WORKSPACE_DIR:-/workspace}/harnesses/priority_mix.py"