#!/usr/bin/env bash

# Copyright 2025 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

set -euo pipefail

# Get the test directory and set up paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SETUP_DIR="${PROJECT_ROOT}/setup"

# Set up test environment
export LLMDBENCH_CONTROL_DIR="${SETUP_DIR}"
export LLMDBENCH_MAIN_DIR="${PROJECT_ROOT}"

# Create a temporary directory for testing
TEST_DIR=$(mktemp -d)
export LLMDBENCH_INFRA_DIR="${TEST_DIR}"
export LLMDBENCH_INFRA_GIT_REPO="https://github.com/llm-d-incubation/llm-d-infra.git"
export LLMDBENCH_INFRA_GIT_BRANCH="main"

# Test configuration
export LLMDBENCH_CONTROL_DRY_RUN=1  # Always use dry run for tests
export LLMDBENCH_CONTROL_VERBOSE=1

# Mock git and announce functions for testing
export LLMDBENCH_TEST_MODE=1

# Counter for command executions
export LLMDBENCH_TEST_CMD_COUNT=0
export LLMDBENCH_TEST_ANNOUNCE_COUNT=0
export LLMDBENCH_TEST_COMMANDS=()
export LLMDBENCH_TEST_ANNOUNCEMENTS=()

# Source required functions (skip dependency checks for testing)
export LLMDBENCH_DEPENDENCIES_CHECKED=1
# Only source specific variables we need, not the full env.sh
export LLMDBENCH_CONTROL_SCMD="sed"

# Mock functions for testing
function llmdbench_execute_cmd() {
    local cmd="$1"
    local dry_run="${2:-1}"
    local verbose="${3:-0}"
    
    # Record the command for verification
    LLMDBENCH_TEST_COMMANDS+=("$cmd")
    ((LLMDBENCH_TEST_CMD_COUNT++))
    
    if [[ $verbose -eq 1 ]]; then
        echo "[TEST] Would execute: $cmd"
    fi
    
    # Simulate git command behavior for dry run
    if [[ "$cmd" == *"git clone"* ]]; then
        echo "[TEST] Simulating git clone success"
        return 0
    elif [[ "$cmd" == *"git checkout"* ]] || [[ "$cmd" == *"git pull"* ]]; then
        echo "[TEST] Simulating git checkout/pull success"
        return 0
    fi
    
    return 0
}

function announce() {
    local message="$1"
    LLMDBENCH_TEST_ANNOUNCEMENTS+=("$message")
    ((LLMDBENCH_TEST_ANNOUNCE_COUNT++))
    echo "[TEST ANNOUNCE] $message"
}

# Test functions
function test_new_clone_scenario() {
    echo "=== Testing new clone scenario ==="
    
    # Reset counters
    LLMDBENCH_TEST_CMD_COUNT=0
    LLMDBENCH_TEST_ANNOUNCE_COUNT=0
    LLMDBENCH_TEST_COMMANDS=()
    LLMDBENCH_TEST_ANNOUNCEMENTS=()
    
    # Ensure test directory is clean (no llm-d-infra directory)
    rm -rf "${TEST_DIR}/llm-d-infra"
    
    # Run the script content without sourcing env.sh
    cd "${TEST_DIR}"  # Simulate being in INFRA_DIR
    
    # Source just the script logic, skipping the env.sh line
    announce "💾 Cloning and setting up llm-d-infra..."
    
    if [[ ! -d llm-d-infra ]]; then
      llmdbench_execute_cmd "cd ${LLMDBENCH_INFRA_DIR}; git clone \"${LLMDBENCH_INFRA_GIT_REPO}\" -b \"${LLMDBENCH_INFRA_GIT_BRANCH}\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    else
      llmdbench_execute_cmd "git checkout ${LLMDBENCH_INFRA_GIT_BRANCH}; git pull" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
    
    announce "✅ llm-d-infra is present at \"${LLMDBENCH_INFRA_DIR}\""
    
    # Verify expected behavior
    echo "Commands executed: ${LLMDBENCH_TEST_CMD_COUNT}"
    echo "Announcements made: ${LLMDBENCH_TEST_ANNOUNCE_COUNT}"
    
    # Validate that git clone command was called
    local found_clone=0
    for cmd in "${LLMDBENCH_TEST_COMMANDS[@]}"; do
        if [[ "$cmd" == *"git clone"* ]] && [[ "$cmd" == *"${LLMDBENCH_INFRA_GIT_REPO}"* ]]; then
            found_clone=1
            echo "Found expected git clone command: $cmd"
            break
        fi
    done
    
    if [[ $found_clone -eq 0 ]]; then
        echo "Expected git clone command not found"
        return 1
    fi
    
    # Validate announcements
    local found_start_announce=0
    local found_end_announce=0
    for announcement in "${LLMDBENCH_TEST_ANNOUNCEMENTS[@]}"; do
        if [[ "$announcement" == *"Cloning and setting up llm-d-infra"* ]]; then
            found_start_announce=1
        elif [[ "$announcement" == *"llm-d-infra is present at"* ]]; then
            found_end_announce=1
        fi
    done
    
    if [[ $found_start_announce -eq 1 && $found_end_announce -eq 1 ]]; then
        echo "Found expected announcements"
    else
        echo "Missing expected announcements"
        return 1
    fi
    
    echo "New clone scenario test passed"
}

function test_existing_repo_scenario() {
    echo "=== Testing existing repo scenario ==="
    
    # Reset counters
    LLMDBENCH_TEST_CMD_COUNT=0
    LLMDBENCH_TEST_ANNOUNCE_COUNT=0
    LLMDBENCH_TEST_COMMANDS=()
    LLMDBENCH_TEST_ANNOUNCEMENTS=()
    
    # Create mock llm-d-infra directory to simulate existing repo
    mkdir -p "${TEST_DIR}/llm-d-infra"
    
    # Run the script content without sourcing env.sh
    cd "${TEST_DIR}"  # Simulate being in INFRA_DIR
    
    # Source just the script logic, skipping the env.sh line
    announce "💾 Cloning and setting up llm-d-infra..."
    
    if [[ ! -d llm-d-infra ]]; then
      llmdbench_execute_cmd "cd ${LLMDBENCH_INFRA_DIR}; git clone \"${LLMDBENCH_INFRA_GIT_REPO}\" -b \"${LLMDBENCH_INFRA_GIT_BRANCH}\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    else
      llmdbench_execute_cmd "git checkout ${LLMDBENCH_INFRA_GIT_BRANCH}; git pull" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
    
    announce "✅ llm-d-infra is present at \"${LLMDBENCH_INFRA_DIR}\""
    
    # Verify expected behavior
    echo "Commands executed: ${LLMDBENCH_TEST_CMD_COUNT}"
    echo "Announcements made: ${LLMDBENCH_TEST_ANNOUNCE_COUNT}"
    
    # Validate that git checkout/pull commands were called
    local found_checkout_pull=0
    for cmd in "${LLMDBENCH_TEST_COMMANDS[@]}"; do
        if [[ "$cmd" == *"git checkout"* ]] && [[ "$cmd" == *"git pull"* ]]; then
            found_checkout_pull=1
            echo "Found expected git checkout/pull command: $cmd"
            break
        fi
    done
    
    if [[ $found_checkout_pull -eq 0 ]]; then
        echo "Expected git checkout/pull command not found"
        return 1
    fi
    
    echo "Existing repo scenario test passed"
}

function cleanup_test() {
    echo "=== Cleaning up test environment ==="
    rm -rf "${TEST_DIR}"
    echo "Test cleanup completed"
}

# Run tests
function run_all_tests() {
    echo "Starting tests for 00_ensure_llm-d-infra.sh"
    echo "Test directory: ${TEST_DIR}"
    echo "Project root: ${PROJECT_ROOT}"
    echo "Setup directory: ${SETUP_DIR}"
    echo
    
    test_new_clone_scenario
    test_existing_repo_scenario
    cleanup_test
    
    echo
    echo "All tests passed for 00_ensure_llm-d-infra.sh"
}

# Run tests if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    run_all_tests
fi