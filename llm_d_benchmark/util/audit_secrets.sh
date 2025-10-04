#!/usr/bin/env bash

if [ $0 != "-bash" ] ; then
    pushd `dirname "$0"` >/dev/null 2>&1
fi
export LLMDBENCH_BASE_DIR=$(pwd)/..
if [ $0 != "-bash" ] ; then
    popd >/dev/null 2>&1
fi


function quit_env {
    deactivate
    popd >/dev/null 2>&1
    exit $1
}


function rerun_hook {
    echo "Refreshing baseline file using prehook"
    pre-commit run detect-secrets
    if [[ $? -ne 0 ]]; then
        echo "First rescan failed (expected). Adding new baseline and rerunning..."
        git add .secrets.baseline
        pre-commit run detect-secrets
        if [[ $? -ne 0 ]]; then
            echo "Failed even after adding and rerun. Giving up."
            return 1
        fi
    else
        echo "done"
    fi
    return 0
}

pushd ${LLMDBENCH_BASE_DIR} >/dev/null 2>&1

source venv/bin/activate

echo "re-scanning detected secrets..."
detect-secrets scan --update .secrets.baseline
if [[ $? -ne 0 ]]; then
    echo "Failed to scan for secrets."
    quit_env 1
else
    echo "done"
fi

if [[ "${1}" != "force" ]]; then
    echo "Check whether anything new and relevant was detected. Use 'force' to skip this test and audit anyway."
    git diff .secrets.baseline | grep '^[+-]' | grep -e 'is_secret' -e 'is_verified'
    if [[ $? -ne 0 ]]; then
        echo "Nothing to audit. No new secrets added"
        rerun_hook
        quit_env $?
    fi
fi

echo "Running audit for any unclassified detections..."
detect-secrets audit .secrets.baseline
if [[ $? -ne 0 ]]; then
    echo "Failed to audit secrets."
    quit_env 1
else
    echo "done"
fi

echo "Adding .secrets.baseline to staged files..."
git add .secrets.baseline
if [[ $? -ne 0 ]]; then
    echo "Failed to add updated file to staging. You might have to do it manually."
    quit_env 1
else
    echo "done"
fi

rerun_hook

quit_env $?
