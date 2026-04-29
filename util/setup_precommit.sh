#!/usr/bin/env bash
# -----------------------------------------------------------------------
# setup_precommit.sh
#
# Bootstraps the pre-commit framework for this repository by delegating
# all "install llmdbenchmark + system tools + .venv" work to the canonical
# project installer (./install.sh), then adding the pre-commit-specific
# Python packages on top.
#
# Why delegate to install.sh?
#   - It installs the system tools (helm, helmfile, kubectl, skopeo,
#     crane, helm-diff, ...) that the render-validation hooks need.
#   - It creates + reuses .venv/ and installs llmdbenchmark + planner,
#     and it caches its work in ~/.llmdbench_dependencies_checked
#     so repeat runs skip already-verified dependencies.
#   - It is the same bootstrap CI runs, minus the -y flag. CI passes -y
#     to force system Python (the GitHub runner is already isolated), but
#     for local development we want a real virtualenv so hook execution
#     stays reproducible and does not pollute the user's system Python.
#
# Installs only the pre-commit stage. CI is the gate before push, so we
# do not duplicate the local hooks at push time. The exhaustive per-spec
# render lives in CI.
# -----------------------------------------------------------------------
set -euo pipefail

# Resolve repo root (the parent of util/)
if [[ $0 != "-bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi
REPO_ROOT="$(realpath "${SCRIPT_DIR}/..")"

pushd "$REPO_ROOT" > /dev/null

# ---------------------------------------------------------------------
# 1. Delegate to install.sh -- creates .venv, installs llmdbenchmark,
#    planner, all system tools, and verifies imports. Matches CI
#    exactly and uses its own ~/.llmdbench_dependencies_checked cache to
#    skip already-verified dependencies on repeat invocations.
# ---------------------------------------------------------------------
if [[ ! -x ./install.sh ]]; then
    echo "ERROR: ./install.sh not found or not executable at ${REPO_ROOT}/install.sh"
    exit 1
fi

echo "==> Running ./install.sh to provision .venv and system tools..."
# Note: no -y flag. We deliberately want install.sh to create/reuse
# .venv/ instead of falling back to system Python, so the pre-commit
# hooks run in an isolated, reproducible environment.
./install.sh

# install.sh ensures .venv/ exists; activate it so the pre-commit install
# lands in the same venv the hooks will execute from.
# shellcheck disable=SC1091
source .venv/bin/activate

# ---------------------------------------------------------------------
# 2. Install the pre-commit-only extras (pre-commit framework, pytest,
#    detect-secrets plugin) on top of whatever install.sh already put in
#    the venv. install.sh does not install pytest because it is a dev
#    dependency, and pre-commit itself lives in .pre-commit_requirements.txt.
# ---------------------------------------------------------------------
echo ""
echo "==> Installing pre-commit framework and dev extras..."
pip3 install --upgrade pip
pip3 install -r .pre-commit_requirements.txt

# ---------------------------------------------------------------------
# 3. Register the pre-commit hook only. We deliberately don't register
#    a pre-push hook -- CI runs the full per-spec validation, so the
#    push-time gate would just duplicate work.
# ---------------------------------------------------------------------
echo ""
echo "==> Registering pre-commit hook..."
pre-commit install

echo ""
echo "pre-commit hook installed."
echo "  pre-commit: py-compile, pytest, render-validation-changed, generate-sbom, detect-secrets"
echo ""
echo "Run 'pre-commit run --all-files' to exercise the hooks now."

popd > /dev/null
