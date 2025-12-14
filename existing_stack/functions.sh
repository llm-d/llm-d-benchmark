if uname -s | grep -qi darwin; then
  alias sed=gsed  
fi

# Constants
HARNESS_POD_LABEL="llmdbench-harness-launcher"
HARNESS_EXECUTABLE="llm-d-benchmark.sh"
HARNESS_CPU_NR=16
HARNESS_CPU_MEM=32Gi
RESULTS_DIR_PREFIX=/requests
CONTROL_WAIT_TIMEOUT=180

# Log announcement function
function announce {
    # 1 - MESSAGE
    # 2 - LOGFILE

    local message=$(echo ${1})
    local logfile=${2:-none}

    case ${logfile} in
        none|""|"1")
            echo -e "==> $(date) - ${0} - $message"
            ;;
        silent|"0")
            ;;
        *)
            echo -e "==> $(date) - ${0} - $message" >> ${logfile}
            ;;  
    esac
}
export -f announce

# Sanitize pod name to conform to Kubernetes naming conventions
# @TODO Check for additional k8s naming restrictions
function sanitize_pod_name {
  tr [:upper:] [:lower:] <<<"$1" | sed -e 's/[^0-9a-z-][^0-9a-z-]*/-/g' | sed -e 's/^-*//' | sed -e 's/-*$//'
}
export -f sanitize_pod_name

# Sanitize directory name to conform to filesystem naming conventions
function sanitize_dir_name {
  sed -e 's/[^0-9A-Za-z_-][^0-9A-Za-z_-]*/_/g' <<<"$1"
}
export -f sanitize_dir_name

# Generate results directory name
function results_dir_name {
  local stack_name="$1"
  local harness_name="$2"
  local experiment_id="$3"
  local workload_name="${4:+_$4}"

  sanitize_dir_name "${RESULTS_DIR_PREFIX}/${harness_name}_${experiment_id}${workload_name}_${stack_name}"
} 
export -f results_dir_name  

# Retrieve list of available harnesses
function get_harness_list {
  ls ${LLMDBENCH_MAIN_DIR}/workload/harnesses | $LLMDBENCH_CONTROL_SCMD -e 's^inference-perf^inference_perf^' -e 's^vllm-benchmark^vllm_benchmark^' | cut -d '-' -f 1 | $LLMDBENCH_CONTROL_SCMD -n -e 's^inference_perf^inference-perf^' -e 's^vllm_benchmark^vllm-benchmark^' -e 'H;${x;s/\n/,/g;s/^,//;p;}'
}
export -f get_harness_list

function start_harness_pod {

  local pod_name=$1
  local harness_dataset_file=${harness_dataset_path##*/}
  local harness_dataset_dir=${harness_dataset_path%/$harness_dataset_file}
  # run_experiment_results_dir=$(results_dir_name "${endpoint_stack_name}" "${harness_name}" "${_uid}")
  experiment_analyzer=$(find ${_root_dir}/analysis/ -name ${harness_name}* | rev | cut -d '/' -f1 | rev)

  ${control_kubectl} --namespace ${harness_namespace} delete pod ${pod_name} --ignore-not-found

  cat <<EOF | yq '.spec.containers[0].env = load("'${_config_file}'").env + .spec.containers[0].env' | ${control_kubectl} apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: ${harness_namespace}
  labels:
    app: ${HARNESS_POD_LABEL}
spec:
  containers:
  - name: harness
    image: ${harness_image}
    imagePullPolicy: Always
    securityContext:
      runAsUser: 0
    command: ["sh", "-c"]
    args:
    - "sleep 1000000"
    resources:
      limits:
        cpu: "${HARNESS_CPU_NR}"
        memory: ${HARNESS_CPU_MEM}
      requests:
        cpu: "${HARNESS_CPU_NR}"
        memory: ${HARNESS_CPU_MEM}
    env:
    # - name: LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR
    #   value: "NOT USING AUTO"
    # - name: RAYON_NUM_THREADS
    #   value: "4"
    - name: LLMDBENCH_RUN_WORKSPACE_DIR
      value: "/workspace"
    # - name: LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME
    #   value: "NOT USING AUTO"
    # - name: LLMDBENCH_RUN_EXPERIMENT_HARNESS
    #   value: "NOT USING AUTO"
    # - name: LLMDBENCH_RUN_EXPERIMENT_ANALYZER
    #   value: "NOT USING AUTO"
    - name: LLMDBENCH_MAGIC_ENVAR
      value: "harness_pod"
    # - name: LLMDBENCH_DEPLOY_METHODS
    #   value: ""
    - name: LLMDBENCH_HARNESS_NAME
      value: "${harness_name}"
    # - name: LLMDBENCH_RUN_EXPERIMENT_ID
    #   value: "${_uid}"
    - name: LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX
      value: "${RESULTS_DIR_PREFIX}"
    # - name: LLMDBENCH_BASE64_CONTEXT_CONTENTS
    #   value: "DO NOT TRANSFER FOR NOW"
    # - name: LLMDBENCH_RUN_DATASET_DIR
    #   value: "DO NOT TRANSFER FOR NOW"
    # - name: LLMDBENCH_RUN_DATASET_URL
    #   value: "DO NOT TRANSFER FOR NOW"
    - name: LLMDBENCH_HARNESS_STACK_NAME
      value: "${endpoint_stack_name}"  
    # - name: HF_TOKEN_SECRET
    #   value: "${endpoint_hf_token_secret}"
    # - name: HUGGING_FACE_HUB_TOKEN
    #   valueFrom:
    #     secretKeyRef:
    #       name: ${endpoint_hf_token_secret}
    #       key: HF_TOKEN
    # - name: POD_NAME
    #   valueFrom:
    #     fieldRef:
    #       fieldPath: metadata.name
    volumeMounts:
    - name: results
      mountPath: ${RESULTS_DIR_PREFIX}
    - name: "${harness_name}-profiles"
      mountPath: /workspace/profiles/${harness_name}  
  volumes:
  - name: results
    persistentVolumeClaim:
      claimName: $harness_results_pvc
  - name: ${harness_name}-profiles    
    configMap:
      name: ${harness_name}-profiles
  restartPolicy: Never    
EOF

  echo ${control_kubectl} wait --for=condition=Ready=True pod ${pod_name} -n ${harness_namespace} --timeout="${CONTROL_WAIT_TIMEOUT}s"

  ${control_kubectl} wait --for=condition=Ready=True pod ${pod_name} -n ${harness_namespace} --timeout="${CONTROL_WAIT_TIMEOUT}s"
  if [[ $? != 0 ]]; then
    announce "❌ Timeout waiting for pod ${pod_name} to get ready"
    exit 1
  fi
  announce "ℹ️ Harness pod ${pod_name} started"
  ${control_kubectl} describe pod ${pod_name} -n ${harness_namespace}
}
