#!/usr/bin/env bash
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_LOADGEN_EC=1
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_REPORT_EC=1

export LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO=1
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_AUTO=1
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_MAX_TRIES=${LLMDBENCH_RUN_EXPERIMENT_HARNESS_MAX_TRIES:-3}

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
  export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX=$(echo $LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed "s^-llm-d-benchmark^^g" | cut -d '.' -f 1)_${LLMDBENCH_RUN_EXPERIMENT_ID}_${LLMDBENCH_HARNESS_STACK_NAME}
  export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX/$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX
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

if [[ ! -z $LLMDBENCH_RUN_DATASET_DIR ]]; then
  mkdir -p $LLMDBENCH_RUN_DATASET_DIR
fi

if [[ ! -z $LLMDBENCH_RUN_DATASET_URL ]]; then
  pushd $LLMDBENCH_RUN_DATASET_DIR > /dev/null 2>&1
  wget --no-clobber ${LLMDBENCH_RUN_DATASET_URL}
  popd > /dev/null 2>&1
fi

env | grep ^LLMDBENCH | grep -v BASE64 | sort

# Scrape vLLM /metrics from all serving pods in the namespace.
# Usage: scrape_vllm_metrics <phase>  (phase = "pre" or "post")
function scrape_vllm_metrics {
  local phase=$1
  local namespace=${LLMDBENCH_VLLM_COMMON_NAMESPACE:-llmdbench}
  local metrics_port=${LLMDBENCH_VLLM_COMMON_METRICS_PORT:-8200}
  local inference_port=${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT:-8000}
  local metrics_path=${LLMDBENCH_VLLM_MONITORING_METRICS_PATH:-/metrics}
  local metrics_dir="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}/vllm_metrics"
  local timestamp
  timestamp=$(date --iso-8601=seconds 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%S%z")

  mkdir -p "${metrics_dir}"
  echo "Scraping vLLM ${phase} metrics (namespace=${namespace}, port=${metrics_port}, fallback_port=${inference_port})..."

  # Try modelservice labels first, then standalone
  local pod_info
  pod_info=$(kubectl --namespace "$namespace" get pods \
    -l llm-d.ai/inferenceServing=true \
    --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{" "}{.metadata.labels.llm-d\.ai/role}{"\n"}{end}' 2>/dev/null || true)

  if [[ -z "$pod_info" ]]; then
    pod_info=$(kubectl --namespace "$namespace" get pods \
      -l stood-up-via=standalone \
      --field-selector=status.phase=Running \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{" "}{"standalone"}{"\n"}{end}' 2>/dev/null || true)
  fi

  if [[ -z "$pod_info" ]]; then
    echo "WARNING: No vLLM pods found for metrics scraping in namespace ${namespace}"
    return 0
  fi

  echo "$pod_info" | while read -r pod_name pod_ip role; do
    [[ -z "$pod_ip" || -z "$pod_name" ]] && continue
    local outfile="${metrics_dir}/${phase}_${pod_name}.log"
    echo "  Scraping ${pod_name} (${pod_ip}:${metrics_port}, role=${role})..."
    curl -s --connect-timeout 5 --max-time 30 \
      "http://${pod_ip}:${metrics_port}${metrics_path}" > "$outfile" 2>/dev/null
    # If metrics port fails or returns empty, fall back to inference port (standalone vLLM serves /metrics on --port)
    if [[ ! -s "$outfile" && "$metrics_port" != "$inference_port" ]]; then
      echo "  Retrying ${pod_name} on inference port (${pod_ip}:${inference_port})..."
      curl -s --connect-timeout 5 --max-time 30 \
        "http://${pod_ip}:${inference_port}${metrics_path}" > "$outfile" 2>/dev/null || \
        echo "  WARNING: Failed to scrape metrics from ${pod_name}"
    fi
  done

  cat > "${metrics_dir}/${phase}_metadata.json" <<METAEOF
{
  "phase": "${phase}",
  "timestamp": "${timestamp}",
  "namespace": "${namespace}",
  "metrics_port": ${metrics_port},
  "metrics_path": "${metrics_path}"
}
METAEOF

  echo "vLLM ${phase} metrics scraping complete. Files saved to ${metrics_dir}/"
}

# Scrape vLLM /metrics before benchmark run
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]]; then
  scrape_vllm_metrics "pre" || echo "WARNING: Pre-benchmark metrics scrape failed"
fi

echo "Running harness: /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}"
counter=1
while [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_LOADGEN_EC -ne 0 && "${counter}" -le $LLMDBENCH_RUN_EXPERIMENT_HARNESS_MAX_TRIES ]]; do
  /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}
  ec=$?
  if [[ $ec -ne 0 ]]; then
    echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS} failed, wating 30 seconds and trying again"
    sleep 30
    counter="$(( ${counter} + 1 ))"
    set -x
  else
    export LLMDBENCH_RUN_EXPERIMENT_HARNESS_LOADGEN_EC=0
  fi
done
echo "Harness completed: /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_HARNESS}"

# Scrape vLLM /metrics after benchmark run
if [[ "${LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED:-false}" == "true" ]]; then
  scrape_vllm_metrics "post" || echo "WARNING: Post-benchmark metrics scrape failed"
fi

if [[ -f ~/fixbashrc ]]; then
  mv -f ~/fixbashrc ~/.bashrc
fi

echo "Running analysis: /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}"
counter=1
while [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_REPORT_EC -ne 0 && "${counter}" -le $LLMDBENCH_RUN_EXPERIMENT_HARNESS_MAX_TRIES ]]; do
/usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}
ec=$?
if [[ $ec -ne 0 ]]; then
    echo "execution of /usr/local/bin/${LLMDBENCH_RUN_EXPERIMENT_ANALYZER} failed, wating 30 seconds and trying again"
    sleep 30
    counter="$(( ${counter} + 1 ))"
    set -x
  else
    export LLMDBENCH_RUN_EXPERIMENT_HARNESS_REPORT_EC=0
  fi
done


if [[ $LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO -eq 0 ]]; then
  echo "Done. Data is available at \"$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR\""
fi
# Return with error code of first iteration of experiment analyzer
exit $((LLMDBENCH_RUN_EXPERIMENT_HARNESS_LOADGEN_EC + LLMDBENCH_RUN_EXPERIMENT_HARNESS_REPORT_EC))
