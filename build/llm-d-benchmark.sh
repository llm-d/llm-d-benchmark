#!/usr/bin/env bash
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC=1
 if [[ ! -z $1 ]]; then
  export LLMDBENCH_HARNESS_NAME=${1}
  export LLMDBENCH_RUN_EXPERIMENT_HARNESS=$(find /usr/local/bin | grep ${1}.*-llm-d-benchmark | rev | cut -d '/' -f 1 | rev)
  export LLMDBENCH_RUN_EXPERIMENT_ANALYZER=$(find /usr/local/bin | grep ${1}.*-analyze_results | rev | cut -d '/' -f 1 | rev)
  export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=/requests/$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed "s^-llm-d-benchmark^^g" | cut -d '.' -f 1)_${LLMDBENCH_RUN_EXPERIMENT_ID}_${LLMDBENCH_HARNESS_STACK_NAME}
fi
if [[ ! -z $2 ]]; then
  export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=$2
else
  if [[ ! -z ${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} ]]; then
    echo ${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} | base64 -d > /workspace/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
  fi
fi

export LLMDBENCH_HARNESS_GIT_REPO=$(cat /workspace/repos.txt | grep ^${LLMDBENCH_HARNESS_NAME}: | cut -d ":" -f 2,3 | cut -d ' ' -f 2 | tr -d ' ')
export LLMDBENCH_HARNESS_GIT_BRANCH=$(cat /workspace/repos.txt | grep ^${LLMDBENCH_HARNESS_NAME}: | cut -d " " -f 3 | tr -d ' ')

export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME".yaml" | sed "s^.yaml.yaml^.yaml^g")
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR=$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed "s^-llm-d-benchmark^^g" | cut -d '.' -f 1)

mkdir -p ~/.kube
if [[ ! -z ${LLMDBENCH_BASE64_CONTEXT_CONTENTS} ]]; then
  echo ${LLMDBENCH_BASE64_CONTEXT_CONTENTS} | base64 -d > ~/.kube/config
fi

if [[ -f ~/.bashrc ]]; then
  mv -f ~/.bashrc ~/fixbashrc
fi

#if [[ -d $LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR && ! -z $LLMDBENCH_HARNESS_GIT_REPO ]]; then
#  pushd /workspace/$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR
#  current_repo=$(git remote -v | grep \(fetch\) | awk '{ print $2 }')
#  if [[ $current_repo == $LLMDBENCH_HARNESS_GIT_REPO ]]; then
#    export LLMDBENCH_RUN_EXPERIMENT_HARNESS_CURRENT_COMMIT=$(git rev-parse --short HEAD)
#    git fetch
#  else
#    popd
#    rm -rf /workspace/$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR
#    git clone $LLMDBENCH_HARNESS_GIT_REPO
#    pushd /workspace/$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR
#  fi
#  git checkout $LLMDBENCH_HARNESS_GIT_BRANCH
#  if [[ $(git rev-parse --short HEAD) != ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_CURRENT_COMMIT} ]]; then
#    case ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR} in
#      fmperf*)
#        pip install --no-cache-dir -r requirements.txt && pip install  .
#        ;;
#      inference-perf*)
#        pip install  .
#        ;;
#      vllm-benchmark*)
#        VLLM_USE_PRECOMPILED=1 pip install  .
#        pushd ..
#        if [[ ! -d vllm ]]; then
#          mv -f vllm vllm-benchmark
#        fi
#        popd
#        ;;
#      guidellm*)
#        pip install  .
#        ;;
#    esac
#  fi
#  popd
#fi

if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC -ne 0 ]]; then
  /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}
  ec=$?
  if [[ $ec -ne 0 ]]; then
    echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS} failed, wating 120 seconds and trying again"
    sleep 120
    set -x
  else
    export LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC=0
  fi
fi

if [[ -f ~/fixbashrc ]]; then
  mv -f ~/fixbashrc ~/.bashrc
fi

/usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}
ec=$?
if [[ $ec -ne 0 ]]; then
  echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER} failed, wating 120 seconds and trying again"
  sleep 120
  set -x
fi
exit $ec
