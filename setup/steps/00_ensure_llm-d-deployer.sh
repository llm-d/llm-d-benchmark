#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

announce "💾 Cloning and setting up llm-d-deployer..."

pushd $LLMDBENCH_DEPLOYER_DIR &>/dev/null
if [[ ! -d llm-d-deployer ]]; then
  llmdbench_execute_cmd "cd ${LLMDBENCH_DEPLOYER_DIR}; git clone \"${LLMDBENCH_DEPLOYER_GIT_REPO}\" -b \"${LLMDBENCH_DEPLOYER_GIT_BRANCH}\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "cd ${LLMDBENCH_DEPLOYER_DIR}/llm-d-deployer; patch -p1 < ${LLMDBENCH_MAIN_DIR}/util/patches/llm-d-deployer.patch" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
else
  pushd llm-d-deployer &>/dev/null
#  llmdbench_execute_cmd "cd ${LLMDBENCH_DEPLOYER_DIR}/llm-d-deployer; git checkout ${LLMDBENCH_DEPLOYER_GIT_BRANCH}; git pull" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  popd &>/dev/null
fi
popd &>/dev/null

announce "✅ llm-d-deployer is present at \"${LLMDBENCH_DEPLOYER_DIR}\""