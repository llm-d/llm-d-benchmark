function announce {
    # 1 - MESSAGE
    # 2 - LOGFILE
    local message=$(echo "${1}" | tr '\n' ' ' | $LLMDBENCH_CONTROL_SCMD "s/\t\t*/ /g")
    local logfile=${2:-1}

    if [[ ! -z ${logfile} ]]
    then
        if [[ ${logfile} == "silent" || ${logfile} -eq 0 ]]
        then
            echo -e "==> $(date) - ${0} - $message" >> /dev/null
        elif [[ ${logfile} -eq 1 ]]
        then
            echo -e "==> $(date) - ${0} - $message"
        else
            echo -e "==> $(date) - ${0} - $message" >> ${logfile}
        fi
    else
        echo -e "==> $(date) - ${0} - $message"
    fi
}
export -f announce

function model_attribute {
  local model=$1
  local attribute=$2

  # Do not use associative arrays. Not supported by MacOS with older bash versions

  case "$model" in
    "llama-1b") local model=meta-llama/Llama-3.2-1B-Instruct ;;
    "llama-3b") local model=meta-llama/Llama-3.2-3B-Instruct ;;
    "llama-8b") local model=meta-llama/Llama-3.1-8B-Instruct ;;
    "llama-70b") local model=meta-llama/Llama-3.1-70B-Instruct ;;
    "llama-17b") local model=meta-llama/Llama-4-Scout-17B-16E-Instruct ;;
    *)
      true ;;
  esac

  local modelcomponents=$(echo $model | cut -d '/' -f 2 |  tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e 's^qwen^qwen-^g' -e 's^-^\n^g')
  local provider=$(echo $model | cut -d '/' -f 1)
  local type=$(echo "${modelcomponents}" | grep -Ei "nstruct|hf|chat|speech|vision")
  local parameters=$(echo "${modelcomponents}" | grep -Ei "[0-9].*b|[0-9].*m" | $LLMDBENCH_CONTROL_SCMD -e 's^a^^' -e 's^\.^p^')
  local majorversion=$(echo "${modelcomponents}" | grep -Ei "^[0-9]" | grep -Evi "b|E" |  $LLMDBENCH_CONTROL_SCMD -e "s/$parameters//g" | cut -d '.' -f 1)
  local kind=$(echo "${modelcomponents}" | head -n 1 | cut -d '/' -f 1)
  local as_label=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e "s^/^-^g")
  local label=$(echo ${kind}-${majorversion}-${parameters} | $LLMDBENCH_CONTROL_SCMD -e 's^-$^^g' -e 's^--^^g')
  local as_label=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e "s^/^-^g" -e "s^\.^-^g")
  local folder=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e 's^/^_^g' -e 's^-^_^g')

  if [[ $attribute != "model" ]];
  then
    echo ${!attribute} | tr '[:upper:]' '[:lower:]'
  else
    echo ${!attribute}
  fi
}
export -f model_attribute

function get_model_aliases_list {
  cat ${LLMDBENCH_MAIN_DIR}/setup/functions.sh | grep -v /setup/functions.sh | grep ") local model=" | $LLMDBENCH_CONTROL_SCMD -e 's^ "^                 "^g' -e "s^) local model=^ -> ^g" -e "s^ ;;^^g"
}
export -f get_model_aliases_list

function resolve_harness_git_repo {
  local harness_name=$1

  if [[ $LLMDBENCH_HARNESS_GIT_REPO == "auto" ]]; then
    case "$harness_name" in
      "fmperf") echo "https://github.com/fmperf-project/fmperf.git" ;;
      "vllm"|"vllm-benchmark") echo "https://github.com/vllm-project/vllm.git";;
      "inference-perf") echo "https://github.com/kubernetes-sigs/inference-perf.git";;
      "guidellm") echo "https://github.com/vllm-project/guidellm.git";;
      *)
          echo "Unknown harness: $harness_name"
          exit 1;;
    esac
  else
    echo "${LLMDBENCH_HARNESS_GIT_REPO}"
  fi
}
export -f resolve_harness_git_repo

function get_image {
  local image_registry=$1
  local image_repo=$2
  local image_name=$3
  local image_tag=$4
  local tag_only=${5:-0}

  is_latest_tag=$image_tag
  if [[ $image_tag == "auto" ]]; then
    if [[ $LLMDBENCH_CONTROL_CCMD == "podman" ]]; then
      is_latest_tag=$($LLMDBENCH_CONTROL_CCMD search --list-tags ${image_registry}/${image_repo}/${image_name} | tail -1 | awk '{ print $2 }' || true)
    else
      is_latest_tag=$(skopeo list-tags docker://${image_registry}/${image_repo}/${image_name} | jq -r .Tags[] | tail -1)
    fi
    if [[ -z ${is_latest_tag} ]]; then
      announce "❌ Unable to find latest tag for image \"${image_registry}/${image_repo}/${image_name}\""
      exit 1
    fi
  fi
  if [[ $tag_only -eq 1 ]]; then
    echo ${is_latest_tag}
  else
    echo $image_registry/$image_repo/${image_name}:${is_latest_tag}
  fi
}

export -f get_image

function prepare_work_dir {
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/environment
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/harnesses
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles
  for profile_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
    mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$profile_type
  done
}
export -f prepare_work_dir

function llmdbench_execute_cmd {
  set +euo pipefail
  local actual_cmd=$1
  local dry_run=${2:-1}
  local verbose=${3:-0}
  local silent=${4:-1}
  local attempts=${5:-1}
  local fatal=${6:-0}
  local counter=1
  local delay=10

  command_tstamp=$(date +%s%N)
  if [[ ${dry_run} -eq 1 ]]; then
    _msg="---> would have executed the command \"${actual_cmd}\""
    echo ${_msg}
    echo ${_msg} > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_command.log
    return 0
  else
    _msg="---> will execute the command \"${actual_cmd}\""
    echo ${_msg} > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_command.log
    while [[ "${counter}" -le "${attempts}" ]]; do
      command_tstamp=$(date +%s%N)
      if [[ ${verbose} -eq 0 && ${silent} -eq 1 ]]; then
        eval ${actual_cmd} 2> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stderr.log 1> ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stdout.log
        local ecode=$?
      elif [[ ${verbose} -eq 0 && ${silent} -eq 0 ]]; then
        eval ${actual_cmd}
        local ecode=$?
      else
        echo ${_msg}
        eval ${actual_cmd}
        local ecode=$?
      fi

      if [[ $ecode -ne 0 && ${attempts} -gt 1 ]]
      then
        counter="$(( ${counter} + 1 ))"
        sleep ${delay}
      else
          break
      fi
    done
  fi

  if [[ $ecode -ne 0 ]]
  then
    echo "ERROR while executing command \"${actual_cmd}\""
    echo
    if [[ -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stdout.log ]]; then
      cat ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stdout.log
    else
      echo "(stdout not captured)"
    fi
    if [[ -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stderr.log ]]; then
      cat ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands/${command_tstamp}_stderr.log
    else
      echo "(stderr not captured)"
    fi
  fi

  set -euo pipefail

  if [[ ${fatal} -eq 1 ]];
  then
    if [[ ${ecode} -ne 0 ]]
    then
      exit ${ecode}
    fi
  fi

  return ${ecode}
}
export -f llmdbench_execute_cmd

function extract_environment {
  local envlist=$(env | grep ^LLMDBENCH | sort | grep -Ev "TOKEN|USER|PASSWORD|EMAIL")
  if [[ $LLMDBENCH_CONTROL_ENVVAR_DISPLAYED -eq 0 ]]; then
    echo -e "\n\nList of environment variables which will be used"
    echo "$envlist"
    echo -e "\n\n"
    export LLMDBENCH_CONTROL_ENVVAR_DISPLAYED=1
  fi
  echo "$envlist" > ${LLMDBENCH_CONTROL_WORK_DIR}/environment/variables
}
export -f extract_environment

function reconfigure_gateway_after_deploy {
  if [[ $LLMDBENCH_VLLM_MODELSERVICE_RECONFIGURE_GATEWAY_AFTER_DEPLOY -eq 1 ]]; then
    if [[ $LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME == "kgateway" ]]; then
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace kgateway-system delete pod -l kgateway=kgateway" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace kgateway-system  wait --for=condition=Ready=True pod -l kgateway=kgateway" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    fi
  fi
}
export -f reconfigure_gateway_after_deploy

function add_annotations {
  local output="REPLACEFIRSTNEWLINE"
  for entry in $(echo $LLMDBENCH_VLLM_COMMON_ANNOTATIONS | $LLMDBENCH_CONTROL_SCMD -e 's^\,^\n^g'); do
    output=$output"REPLACE_NEWLINEREPLACE_SPACESN$(echo ${entry} | $LLMDBENCH_CONTROL_SCMD -e 's^:^: ^g')"
  done

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
    local spacen="        "
  fi

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
    local spacen="      "
  fi

  echo -e ${output} | $LLMDBENCH_CONTROL_SCMD -e 's^REPLACEFIRSTNEWLINEREPLACE_NEWLINEREPLACE_SPACESN^^' -e 's^REPLACE_NEWLINE^\n^g' -e "s^REPLACE_SPACESN^$spacen^g" -e '/^*$/d'

}

function add_additional_env_to_yaml {
  local output="REPLACEFIRSTNEWLINE"
  for envvar in ${LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML//,/ }; do
    output=$output"REPLACE_NEWLINEREPLACE_SPACESN- name: $(echo ${envvar} | $LLMDBENCH_CONTROL_SCMD -e 's^LLMDBENCH_VLLM_STANDALONE_^^g')REPLACE_NEWLINEREPLACE_SPACESVvalue: \"${!envvar}\""
  done

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 1 ]]; then
    local spacen="        "
    local spacev="          "
  fi

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
    local spacen="      "
    local spacev="        "
  fi

  echo -e ${output} | $LLMDBENCH_CONTROL_SCMD -e 's^REPLACEFIRSTNEWLINEREPLACE_NEWLINEREPLACE_SPACESN^^' -e 's^REPLACE_NEWLINE^\n^g' -e "s^REPLACE_SPACESN^$spacen^g"  -e "s^REPLACE_SPACESV^$spacev^g"  -e '/^*$/d'
}
export -f add_additional_env_to_yaml

function render_string {
  set +euo pipefail
  local string=$1
  local model=${2:-}

  if [[ ! -z $model ]]; then
    echo "s^REPLACE_MODEL^$(model_attribute $model model)^g" > $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  islist=$(echo $string | grep "\[" || true)
  if [[ ! -z $islist ]]; then
    echo "s^____^\", \"^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    echo "s^\[^[ \"^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    echo "s^\]^\" ]^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  else
    echo "s^____^ ^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  for entry in $(echo ${string} | $LLMDBENCH_CONTROL_SCMD -e 's/____/ /g' -e 's^-^\n^g' -e 's^:^\n^g' -e 's^/^\n^g' -e 's^ ^\n^g' -e 's^]^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq); do
    default_value=$(echo $entry | $LLMDBENCH_CONTROL_SCMD -e "s^++++default=^\n^" | tail -1)
    parameter_name=$(echo ${entry} | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_ENV_^\n______^g" -e "s^\"^^g" -e "s^'^^g" | grep "______" | $LLMDBENCH_CONTROL_SCMD -e "s^++++default=.*^^" -e "s^______^^g")
    entry=REPLACE_ENV_${parameter_name}
    value=$(echo ${!parameter_name})
    if [[ -z $value && -z $default_value ]]; then
      announce "❌ ERROR: variable \"$entry\" not defined!"
      exit 1
    fi
    if [[ -z $value && ! -z $default_value ]]; then
      value=$default_value
      echo "s^++++default=$default_value^^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    fi
    echo "s^${entry}^${value}^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  done
  if [[ ! -z $model ]]; then
    echo ${string} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi
  set -euo pipefail
}
export -f render_string

function render_template {
  local template_file_path=$1
  local output_file_path=$2

  rm -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  for entry in $(cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -e 's^-^\n^g' -e 's^:^\n^g' -e 's^ ^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq); do
    render_string $entry
  done
  cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands > $output_file_path
}
export -f render_template

function check_storage_class {
  if [[ ${LLMDBENCH_CONTROL_CALLER} != "standup.sh" && ${LLMDBENCH_CONTROL_CALLER} != "e2e.sh" ]]; then
    return 0
  fi

  if [[ $LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS == "default" ]]; then
    if [[ ${LLMDBENCH_CONTROL_CALLER} == "standup.sh" || ${LLMDBENCH_CONTROL_CALLER} == "e2e.sh" ]]; then
      has_default_sc=$($LLMDBENCH_CONTROL_KCMD get storageclass -o=jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{@.metadata.name}{"\n"}{end}' || true)
      if [[ -z $has_default_sc ]]; then
          announce "❌ ERROR: environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=default, but unable to find a default storage class\""
          exit 1
      fi
      announce "ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS automatically set to \"${has_default_sc}\""
      export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=${has_default_sc}
    fi
  fi

  local has_sc=$($LLMDBENCH_CONTROL_KCMD get storageclasses | grep $LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS || true)
  if [[ -z $has_sc ]]; then
    announce "❌ ERROR. Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=$LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS but could not find such storage class"
    return 1
  fi
}
export -f check_storage_class

function check_affinity {

  local accelerator_string="nvidia.com/gpu.product|gpu.nvidia.com/class|cloud.google.com/gke-accelerator"

  if [[ ${LLMDBENCH_CONTROL_CALLER} != "standup.sh" && ${LLMDBENCH_CONTROL_CALLER} != "e2e.sh" ]]; then
    return 0
  fi

  if [[ ${LLMDBENCH_VLLM_COMMON_AFFINITY} == "auto" ]]; then
    if [[ ${LLMDBENCH_CONTROL_CALLER} == "standup.sh" || ${LLMDBENCH_CONTROL_CALLER} == "e2e.sh" ]]; then
      has_default_accelerator=$($LLMDBENCH_CONTROL_KCMD get nodes -o json | jq -r '.items[].metadata.labels' | grep -E "${accelerator_string}" | tail -1 | $LLMDBENCH_CONTROL_SCMD -e 's^"^^g' -e 's^,^^g' -e 's^ ^^g')
      if [[ -z $has_default_accelerator ]]; then
          announce "❌ ERROR: environment variable LLMDBENCH_VLLM_COMMON_AFFINITY=auto, but unable to find an accelerator on any node\""
          exit 1
      fi
#      export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=$(echo ${has_default_accelerator} | cut -d ':' -f 1)
      export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=nvidia.com/gpu
      export LLMDBENCH_VLLM_COMMON_AFFINITY=$has_default_accelerator
      announce "ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_AFFINITY automatically set to \"${has_default_accelerator}\""
    fi
  else
    local annotation1=$(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
    local annotation2=$(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
    local has_affinity=$($LLMDBENCH_CONTROL_KCMD get nodes -o json | jq -r '.items[].metadata.labels' | grep -E "$annotation1.*$annotation2" || true)
    if [[ -z $has_affinity ]]; then
      announce "❌ ERROR. There are no nodes on this cluster with the label \"${annotation1}:${annotation2}\" (environment variable LLMDBENCH_VLLM_COMMON_AFFINITY)"
      return 1
    fi
  fi

  if [[ $LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE == "auto" ]]; then
#    export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=$(echo ${has_default_accelerator} | cut -d ':' -f 1)
    export LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE=nvidia.com/gpu
    announce "ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE automatically set to \"${LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE}\""
  fi
}
export -f check_affinity

function not_valid_ip {

    local  ip=$1
    local  stat=1

    echo ${ip} | grep -q '/'
    if [[ $? -eq 0 ]]; then
        local ip=$(echo $ip | cut -d '/' -f 1)
    fi

    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        OIFS=$IFS
        IFS='.'
        ip=($ip)
        IFS=$OIFS
        [[ ${ip[0]} -le 255 && ${ip[1]} -le 255 && ${ip[2]} -le 255 && ${ip[3]} -le 255 ]]
        stat=$?
    fi
    if [[ $stat -eq 0 ]]; then
      echo $ip
    fi
}
export -f not_valid_ip

function get_rand_string {
  openssl rand -base64 4 | tr -dc 'a-zA-Z0-9' |tr '[:upper:]' '[:lower:]' | head -c 16
}
export -f get_rand_string

function require_var {
  local var_name="$1"
  local var_value="$2"
  if [[ -z "${var_value}" ]]; then
    announce "❌ Required variable '${var_name}' is empty"
    exit 1
  fi
}
export -f require_var

function create_namespace {
  local kcmd="$1"
  local namespace="$2"
  require_var "namespace" "${namespace}"
  announce "📦 Creating namespace ${namespace}..."

  is_ns=$($LLMDBENCH_CONTROL_KCMD get namespace -o name| grep -E "namespace/${namespace}$" || true)
  if [[ -z ${is_ns} ]]; then
    llmdbench_execute_cmd "${kcmd} create namespace \"${namespace}\" --dry-run=client -o yaml > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_namespace.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    llmdbench_execute_cmd "${kcmd} apply -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_namespace.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "✅ Namespace ready"
  fi
}
export -f create_namespace

function create_or_update_hf_secret {
  local kcmd="$1"
  local namespace="$2"
  local secret_name="$3"
  local secret_key="$4"
  local hf_token="$5"

  require_var "namespace" "${namespace}"
  require_var "secret_name" "${secret_name}"
  require_var "hf_token" "${hf_token}"

  announce "🔐 Creating/updating HF token secret..."

  llmdbench_execute_cmd "${kcmd} delete secret ${secret_name} -n ${namespace} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "${kcmd} create secret generic \"${secret_name}\" --from-literal=\"${secret_key}=${hf_token}\" --dry-run=client -o yaml > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "${kcmd} apply -n "${namespace}" -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_secret.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  announce "✅ HF token secret created"
}
export -f create_or_update_hf_secret

#
# vLLM Model Download Utilities
#

function validate_and_create_pvc {
  local kcmd="$1"
  local namespace="$2"
  local download_model="$3"
  local pvc_name="$4"
  local pvc_size="$5"
  local pvc_class="$6"

  require_var "download_model" "${download_model}"
  require_var "pvc_name" "${pvc_name}"
  require_var "pvc_size" "${pvc_size}"
  require_var "pvc_class" "${pvc_class}"

  announce "💾 Provisioning model storage…"

  if [[ "${download_model}" != */* ]]; then
    announce "❌ '${download_model}' is not in Hugging Face format <org>/<repo>"
    exit 1
  fi

  announce "🔍 Checking storage class '${pvc_class}'..."
  if ! ${kcmd} get storageclass "${pvc_class}" &>/dev/null; then
    announce "❌ StorageClass '${pvc_class}' not found"
    exit 1
  fi

  cat << EOF > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_storage_pvc_setup.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${pvc_name}
spec:
  accessModes:
  - ReadWriteMany
  resources:
    requests:
      storage: ${pvc_size}
  storageClassName: ${pvc_class}
  volumeMode: Filesystem
EOF

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -n ${namespace} -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_storage_pvc_setup.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 1 1
}
export -f validate_and_create_pvc

function launch_download_job {
  local kcmd="$1"
  local namespace="$2"
  local secret_name="$3"
  local download_model="$4"
  local model_path="$5"
  local pvc_name="$6"

  require_var "namespace" "${namespace}"
  require_var "secret_name" "${secret_name}"
  require_var "download_model" "${download_model}"
  require_var "model_path" "${model_path}"
  require_var "pvc_name" "${pvc_name}"

  announce "🚀 Launching model download job..."

cat << EOF > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_download_pod_job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: download-model
spec:
  template:
    spec:
      containers:
        - name: downloader
          image: python:3.10
          command: ["/bin/sh", "-c"]
          args:
            - mkdir -p "\${MOUNT_PATH}/\${MODEL_PATH}" && \
              pip install huggingface_hub && \
              export PATH="\${PATH}:\${HOME}/.local/bin" && \
              huggingface-cli login --token "\${HF_TOKEN}" && \
              huggingface-cli download "\${HF_MODEL_ID}" --local-dir "/cache/\${MODEL_PATH}"
          env:
            - name: MODEL_PATH
              value: ${model_path}
            - name: HF_MODEL_ID
              value: ${download_model}
            - name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: ${secret_name}
                  key: HF_TOKEN
            - name: HF_HOME
              value: /tmp/huggingface
            - name: HOME
              value: /tmp
            - name: MOUNT_PATH
              value: /cache
          volumeMounts:
            - name: model-cache
              mountPath: /cache
      restartPolicy: OnFailure
      imagePullPolicy: IfNotPresent
      volumes:
        - name: model-cache
          persistentVolumeClaim:
            claimName: ${pvc_name}
EOF
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -n ${namespace} -f ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls/${LLMDBENCH_CURRENT_STEP}_download_pod_job.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 1 1
}
export -f launch_download_job

function wait_for_download_job {
  local kcmd="$1"
  local namespace="$2"
  local timeout="$3"

  require_var "namespace" "${namespace}"
  require_var "timeout" "${timeout}"

  announce "⏳ Waiting for pod to start model download job ..."
  local pod_name
  pod_name="$(${kcmd} get pod --selector=job-name=download-model -n "${namespace}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"

  if [[ -z "${pod_name}" ]]; then
    announce "🙀 No pod found for the job. Exiting..."
    llmdbench_execute_cmd "${kcmd} logs job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 1 1
  fi

  llmdbench_execute_cmd "${kcmd} wait --for=condition=Ready pod/"${pod_name}" --timeout=60s -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  if [[ $? -ne 0 ]]
  then
    announce "🙀 Pod did not become Ready"
    llmdbench_execute_cmd  "${kcmd} logs job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0 1 0
    exit 1
  fi

  announce "⏳ Waiting up to ${timeout}s for job to complete..."
  llmdbench_execute_cmd "${kcmd} wait --for=condition=complete --timeout="${timeout}"s job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  if [[ $? -ne 0 ]]
  then
    announce "🙀 Download job failed or timed out"
    llmdbench_execute_cmd  "${kcmd} logs job/download-model -n ${namespace}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0 1 0
    exit 1
  fi

  announce "✅ Model downloaded"
}
export -f wait_for_download_job

function run_step {
  local script_name=$1

  if [[ -f $script_name ]]; then
    local script_path=$script_name
  else
    local script_path=$(ls ${LLMDBENCH_STEPS_DIR}/${script_name}*)
  fi
  if [ -f $script_path ]; then
    local step_id=$(basename "$script_path")
    local step_nr=$(echo $step_id | cut -d '_' -f 1)
    export LLMDBENCH_CURRENT_STEP=${step_nr}
    announce "=== Running step: $step_id ==="
    if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
      echo -e "[DRY RUN] $script_path\n"
    fi
    source $script_path
    echo
  else
    announce "ERROR: unable to run step \"${script_name}\""
  fi
}
export -f run_step

function get_harness_list {
  ls ${LLMDBENCH_MAIN_DIR}/workload/harnesses | $LLMDBENCH_CONTROL_SCMD -e 's^inference-perf^inference_perf^' -e 's^vllm-benchmark^vllm_benchmark^' | cut -d '-' -f 1 | $LLMDBENCH_CONTROL_SCMD -n -e 's^inference_perf^inference-perf^' -e 's^vllm_benchmark^vllm-benchmark^' -e 'H;${x;s/\n/,/g;s/^,//;p;}'
}
export -f get_harness_list

function create_harness_pod {

  is_pvc=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pvc --ignore-not-found | grep ${LLMDBENCH_HARNESS_PVC_NAME} || true)
  if [[ -z ${is_pvc} ]]; then
      announce "❌ PVC \"${LLMDBENCH_HARNESS_PVC_NAME}\" not created on namespace \"${LLMDBENCH_HARNESS_NAMESPACE}\" unable to continue"
      exit 1
  fi

  cat <<EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
apiVersion: v1
kind: Pod
metadata:
  name: ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
  labels:
    app: ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}
spec:
  serviceAccountName: $LLMDBENCH_HARNESS_SERVICE_ACCOUNT
  containers:
  - name: harness
    image: $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG})
    imagePullPolicy: Always
    command: ["sh", "-c"]
    args:
    - "${LLMDBENCH_HARNESS_EXECUTABLE}"
    env:
    - name: LLMDBENCH_RUN_EXPERIMENT_LAUNCHER
      value: "1"
    - name: LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY
      value: "${LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY}"
    - name: LLMDBENCH_HARNESS_GIT_REPO
      value: "$(resolve_harness_git_repo $LLMDBENCH_HARNESS_NAME)"
    - name: LLMDBENCH_HARNESS_GIT_BRANCH
      value: "${LLMDBENCH_HARNESS_GIT_BRANCH}"
    - name: LLMDBENCH_RUN_EXPERIMENT_HARNESS
      value: "${LLMDBENCH_RUN_EXPERIMENT_HARNESS}"
    - name: LLMDBENCH_RUN_EXPERIMENT_ANALYZER
      value: "${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}"
    - name: LLMDBENCH_BASE64_CONTEXT_CONTENTS
      value: "$LLMDBENCH_BASE64_CONTEXT_CONTENTS"
    - name: LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME
      value: "$LLMDBENCH_HARNESS_EXPERIMENT_PROFILE"
    - name: LLMDBENCH_RUN_EXPERIMENT_ID
      value: "${LLMDBENCH_RUN_EXPERIMENT_ID}"
    - name: LLMDBENCH_HARNESS_NAME
      value: "${LLMDBENCH_HARNESS_NAME}"
    - name: LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR
      value: $LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR
    - name: LLMDBENCH_CONTROL_WORK_DIR
      value: "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
    - name: LLMDBENCH_HARNESS_NAMESPACE
      value: "${LLMDBENCH_HARNESS_NAMESPACE}"
    - name: LLMDBENCH_HARNESS_STACK_TYPE
      value: "${LLMDBENCH_HARNESS_STACK_TYPE}"
    - name: LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
      value: "${LLMDBENCH_HARNESS_STACK_ENDPOINT_URL}"
    - name: LLMDBENCH_HARNESS_STACK_NAME
      value: "$LLMDBENCH_HARNESS_STACK_NAME"
    - name: HF_TOKEN_SECRET
      value: "${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}"
    - name: HUGGING_FACE_HUB_TOKEN
      valueFrom:
        secretKeyRef:
          name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
          key: HF_TOKEN
    volumeMounts:
    - name: results
      mountPath: /requests
EOF
  for profile_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
    cat <<EOF >> $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
    - name: ${profile_type}-profiles
      mountPath: /workspace/profiles/${profile_type}
EOF
  done
  cat <<EOF >> $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
  volumes:
  - name: results
    persistentVolumeClaim:
      claimName: $LLMDBENCH_HARNESS_PVC_NAME
EOF
  for profile_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
    cat <<EOF >> $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
  - name: ${profile_type}-profiles
    configMap:
      name: ${profile_type}-profiles
EOF
  done
  cat <<EOF >> $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/pod_benchmark-launcher.yaml
  restartPolicy: Never
EOF
}
export -f create_harness_pod

function cleanup_pre_execution {
  announce "🗑️ Deleting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\"..."
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod ${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete job lmbenchmark-evaluate-${LLMDBENCH_HARNESS_STACK_NAME} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  announce "ℹ️ Done deleting pod \"${LLMDBENCH_RUN_HARNESS_LAUNCHER_NAME}\" (it will be now recreated)"
}
export -f cleanup_pre_execution

function validate_model_name {
  local _model_name=$1
  for mparm in model type parameters majorversion kind label; do
    if [[ -z $(model_attribute ${_model_name} ${mparm}) ]]; then
      announce "❌ Invalid model name \"${_model_name}\""
      exit 1
    fi
  done
}
export -f validate_model_name