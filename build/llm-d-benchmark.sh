#!/usr/bin/env bash
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC=1
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO=1
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_AUTO=1

function show_usage {
    echo -e "Usage: $0 -l/--harness [harness used to generate load (default=$LLMDBENCH_HARNESS_NAME, possible values $(ls $LLMDBENCH_RUN_WORKSPACE_DIR/profiles/ | sed -n ':a;N;$!ba;s/\n/,/g;p')] \n \
                                        -w/--workload [workload to be used by the harness (default=$LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME, possible values (\"ls $LLMDBENCH_RUN_WORKSPACE_DIR/profiles/*/*.yaml\")] \n \
                                        -h/--help (show this help)"
}

while [[ $# -gt 0 ]]
do
    key="$1"

    case $key in
        -l=*|--harness=*)
        export LLMDBENCH_HARNESS_NAME=$(echo $key | cut -d '=' -f 2)
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO=0
        ;;
        -l|--harness)
        export LLMDBENCH_HARNESS_NAME="$2"
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO=0
        shift
        ;;
        -w=*|--workload=*)
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=$(echo $key | cut -d '=' -f 2)
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_AUTO=0
        ;;
        -w|--workload)
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME="$2"
        export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_AUTO=0
        shift
        ;;
        -h|--help)
        show_usage
        if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
        then
            exit 0
        else
            return 0
        fi
        ;;
        *)
        echo "ERROR: unknown option \"$key\""
        show_usage
        exit 1
        ;;
        esac
        shift
done

if [[ ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO} -eq 0 ]]; then
  export LLMDBENCH_RUN_EXPERIMENT_HARNESS=$(find /usr/local/bin | grep ${LLMDBENCH_HARNESS_NAME}.*-llm-d-benchmark | rev | cut -d '/' -f 1 | rev)
  export LLMDBENCH_RUN_EXPERIMENT_ANALYZER=$(find /usr/local/bin | grep ${LLMDBENCH_HARNESS_NAME}.*-analyze_results | rev | cut -d '/' -f 1 | rev)
  export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=/requests/$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed "s^-llm-d-benchmark^^g" | cut -d '.' -f 1)_${LLMDBENCH_RUN_EXPERIMENT_ID}_${LLMDBENCH_HARNESS_STACK_NAME}
  export LLMDBENCH_CONTROL_WORK_DIR=$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR
fi

if [[ ${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_AUTO} -eq 0 ]]; then
  true
else
  if [[ ! -z ${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} ]]; then
    echo ${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} | base64 -d > ${LLMDBENCH_RUN_WORKSPACE_DIR}/${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}
  fi
fi

export LLMDBENCH_HARNESS_GIT_REPO=$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/repos.txt | grep ^${LLMDBENCH_HARNESS_NAME}: | cut -d ":" -f 2,3 | cut -d ' ' -f 2 | tr -d ' ')
export LLMDBENCH_HARNESS_GIT_BRANCH=$(cat ${LLMDBENCH_RUN_WORKSPACE_DIR}/repos.txt | grep ^${LLMDBENCH_HARNESS_NAME}: | cut -d " " -f 3 | tr -d ' ')

export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME".yaml" | sed "s^.yaml.yaml^.yaml^g")
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR=$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed "s^-llm-d-benchmark^^g" | cut -d '.' -f 1)

mkdir -p ~/.kube
if [[ ! -z ${LLMDBENCH_BASE64_CONTEXT_CONTENTS} ]]; then
  echo ${LLMDBENCH_BASE64_CONTEXT_CONTENTS} | base64 -d > ~/.kube/config
fi

if [[ -f ~/.bashrc ]]; then
  mv -f ~/.bashrc ~/fixbashrc
fi

mkdir -p $LLMDBENCH_RUN_DATASET_DIR

if [[ ! -z $LLMDBENCH_RUN_DATASET_URL ]]; then
  pushd $LLMDBENCH_RUN_DATASET_DIR > /dev/null 2>&1
  wget --no-clobber ${LLMDBENCH_RUN_DATASET_URL}
  popd > /dev/null 2>&1
fi

env | grep ^LLMDBENCH | grep -v BASE64 | sort

# Repeat run until success
echo "Running harness: /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}"
while [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC -ne 0 ]]; do
  /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}
  ec=$?
  if [[ $ec -ne 0 ]]; then
    echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS} failed, wating 30 seconds and trying again"
    sleep 30
    set -x
  else
    export LLMDBENCH_RUN_EXPERIMENT_HARNESS_EC=0
  fi
done
echo "Harness completed: /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}"

if [[ -f ~/fixbashrc ]]; then
  mv -f ~/fixbashrc ~/.bashrc
fi

echo "Running analysis: /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}"
# Try to run analysis twice then give up
/usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}
ec=$?
if [[ $ec -ne 0 ]]; then
  echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER} failed, wating 120 seconds and trying again"
  sleep 120
  set -x
  /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}
fi
# Return with error code of first iteration of experiment analyzer
exit $ec
