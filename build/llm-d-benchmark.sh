#!/usr/bin/env bash
 if [[ ! -z $1 ]]; then
  export LLMDBENCH_RUN_EXPERIMENT_HARNESS=$(find /usr/local/bin | grep ${1}.*-llm-d-benchmark | rev | cut -d '/' -f 1 | rev)
  export LLMDBENCH_RUN_EXPERIMENT_ANALYZER=$(find /usr/local/bin | grep ${1}.*-analyze_results | rev | cut -d '/' -f 1 | rev)
  export LLMDBENCH_HARNESS_GIT_REPO=${LLMDBENCH_HARNESS_GIT_REPO-$(cat /workspace/repos.txt | grep ^${1}: | cut -d ":" -f 2,3 | tr -d ' ')}
  export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=/requests/$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed "s^-llm-d-benchmark^^g" | cut -d '.' -f 1)_${LLMDBENCH_RUN_EXPERIMENT_ID}_${LLMDBENCH_HARNESS_STACK_NAME}
fi
if [[ ! -z $2 ]]; then
  export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=$2
else 
  if [[ ! -z ${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} ]]; then
    echo ${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} | base64 -d > /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
  fi
fi
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME".yaml" | sed "s^.yaml.yaml^.yaml^g")
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR=$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed "s^-llm-d-benchmark^^g" | cut -d '.' -f 1)
mkdir -p ~/.kube
if [[ ! -z ${LLMDBENCH_BASE64_CONTEXT_CONTENTS} ]]; then
  echo ${LLMDBENCH_BASE64_CONTEXT_CONTENTS} | base64 -d > ~/.kube/config
fi
if [[ -f ~/.bashrc ]]; then 
  mv -f ~/.bashrc ~/fixbashrc
fi 
if [[ -d $LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR ]]; then 
  pushd /workspace/$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR
  current_repo=$(git remote -v | grep \(fetch\) | awk '{ print $2 }')
  if [[ $current_repo == $LLMDBENCH_HARNESS_GIT_REPO ]]; then
    git fetch
  else
    popd
    rm -rf /workspace/$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR
    git clone $LLMDBENCH_HARNESS_GIT_REPO
    pushd /workspace/$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR
  fi
  git checkout $LLMDBENCH_HARNESS_GIT_BRANCH
  case ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR} in
    fmperf*)
      pip install --no-cache-dir -r requirements.txt && pip install  .
      ;;
    inference-perf*)
      pip install  .
      ;;
    vllm-benchmark*)
      VLLM_USE_PRECOMPILED=1 pip install  .
      pushd ..
      mv -f vllm vllm-benchmark
      popd
      ;;
    guidellm*)
      pip install  .
      ;;
  esac
  popd 
fi
if [[ ! -d /workspace/vllm-benchmark ]]; then
  mv /workspace/vllm /workspace/vllm-benchmark
fi
/usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}
ec=$?
if [[ $ec -ne 0 ]]; then
  echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS} failed, wating 30 seconds and trying again"
  sleep 30
fi
if [[ -f ~/fixbashrc ]]; then 
  mv -f ~/fixbashrc ~/.bashrc
fi 
/usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}
ec=$?
if [[ $ec -ne 0 ]]; then
  echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER} failed, wating 30 seconds and trying again"
  sleep 30
fi
exit $ec
