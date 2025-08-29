#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then

  announce "❌ shell version deprecated. Set LLMDBENCH_CONTROL_STEP_02_IMPLEMENTATION=py"
  exit 1

else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
