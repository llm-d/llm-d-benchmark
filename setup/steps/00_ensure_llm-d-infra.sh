#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED -eq 0 ]]
then
  deplist="$LLMDBENCH_CONTROL_SCMD $LLMDBENCH_CONTROL_PCMD $(echo $LLMDBENCH_CONTROL_KCMD | awk '{ print $1}') $(echo $LLMDBENCH_CONTROL_HCMD | awk '{ print $1}') helmfile kubectl kustomize rsync"
  for req in $deplist kubectl kustomize; do
    echo -n "Checking dependency \"${req}\"..."
    is_req=$(which ${req} || true)
    if [[ -z ${is_req} ]]; then
      echo "❌ Dependency \"${req}\" is missing. Please install it and try again."
      exit 1
    fi
    echo "done"
  done
  echo
  is_helmdiff=$($LLMDBENCH_CONTROL_HCMD plugin list | grep diff || true)
  if [[ -z $is_helmdiff ]]; then
    helm plugin install https://github.com/databus23/helm-diff
  fi
  rm -f ~/.llmdbench_dependencies_checked
  export LLMDBENCH_CONTROL_DEPENDENCIES_CHECKED=1
fi

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
announce "✅ llm-d-infra is present at \"${LLMDBENCH_DEPLOYER_DIR}\""

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