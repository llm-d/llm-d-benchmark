#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

announce "💾 Cloning and setting up llm-d-infra..."
pushd $LLMDBENCH_INFRA_DIR &>/dev/null
if [[ ! -d llm-d-infra ]]; then
  llmdbench_execute_cmd "cd ${LLMDBENCH_INFRA_DIR}; git clone \"${LLMDBENCH_INFRA_GIT_REPO}\" -b \"${LLMDBENCH_INFRA_GIT_BRANCH}\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
else
  pushd llm-d-infra &>/dev/null
  llmdbench_execute_cmd "git checkout ${LLMDBENCH_INFRA_GIT_BRANCH}; git pull" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  popd &>/dev/null
fi
popd &>/dev/null
announce "✅ llm-d-infra is present at \"${LLMDBENCH_INFRA_DIR}\""
