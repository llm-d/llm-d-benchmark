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

  local modelid=$(echo $model | cut -d: -f2 | $LLMDBENCH_CONTROL_SCMD -e "s^/^-^g" -e "s^\.^-^g")
  local SHA256CMD=$(type -p gsha256sum || type -p sha256sum)
  local modelid_label="$(echo -n $modelid | cut -d '/' -f 1 | cut -c1-8)-$(echo -n "$LLMDBENCH_VLLM_COMMON_NAMESPACE/$modelid" | $SHA256CMD | awk '{print $1}' | cut -c1-8)-$(echo -n $modelid | cut -d '/' -f 2 | rev | cut -c1-8 | rev)"

  local modelcomponents=$(echo $model | cut -d '/' -f 2 |  tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e 's^qwen^qwen-^g' -e 's^-^\n^g')
  local provider=$(echo $model | cut -d '/' -f 1)
  local modeltype=$(echo "${modelcomponents}" | grep -Ei "nstruct|hf|chat|speech|vision|opt" || echo base)
  local parameters=$(echo "${modelcomponents}" | grep -Ei "[0-9].*b|[0-9].*m" | $LLMDBENCH_CONTROL_SCMD -e 's^a^^' -e 's^\.^p^' -e 's/[0-9].*p//g' | tail -1)
  if [[ ! -z $parameters ]]; then
    local majorversion=$(echo "${modelcomponents}" | grep -Ei "^[0-9]" | grep -Evi "b|E" |  $LLMDBENCH_CONTROL_SCMD -e "s/$parameters//g" | cut -d '.' -f 1)
  else
    local majorversion=
  fi
  if [[ -z $majorversion ]]; then
    local majorversion=1
  fi
  local kind=$(echo "${modelcomponents}" | head -n 1 | cut -d '/' -f 1)
  local as_label=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e "s^/^-^g")
  local label=$(echo ${kind}-${majorversion}-${parameters} | $LLMDBENCH_CONTROL_SCMD -e 's^-$^^g' -e 's^--^^g')
  local as_label=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e "s^/^-^g" -e "s^\.^-^g")
  local folder=$(echo $model | tr '[:upper:]' '[:lower:]' | $LLMDBENCH_CONTROL_SCMD -e 's^/^_^g' -e 's^-^_^g')
  if [[ $attribute != "model" ]]; then
    echo ${!attribute} | tr '[:upper:]' '[:lower:]'
  else
    echo ${!attribute}
  fi
}
export -f model_attribute

function get_image {
  local image_registry=$1
  local image_repo=$2
  local image_name=$3
  local image_tag=$4
  local tag_only=${5:-0}

  is_latest_tag=$image_tag
  if [[ $image_tag == "auto" ]]; then
    if [[ $LLMDBENCH_CONTROL_CCMD == "podman" ]]; then
      is_latest_tag=$($LLMDBENCH_CONTROL_CCMD search --list-tags --limit 1000 ${image_registry}/${image_repo}/${image_name} | tail -1 | awk '{ print $2 }' || true)
    else
      is_latest_tag=$(skopeo list-tags docker://${image_registry}/${image_repo}/${image_name} | jq -r .Tags[] | tail -1)
    fi
    if [[ -z ${is_latest_tag} ]]; then
      announce "❌ Unable to find latest tag for image \"${image_registry}/${image_repo}/${image_name}\"" >&2
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
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/scenario
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/yamls
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/commands
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/logs
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/experiments
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/environment
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/harnesses
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles
  mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/experiments
}
export -f prepare_work_dir

function llmdbench_execute_cmd {
  local shellsetopts=$(set -o | grep -E "pipefail.*on|errexit.*on|nounset.*on" || true)
  if [[ ! -z ${shellsetopts} ]]; then
    set +euo pipefail
  fi

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

  if [[ ! -z ${shellsetopts} ]]; then
    set -euo pipefail
  fi

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

function render_string {
  set +euo pipefail

  local string=${1}

  if [[ -z $string ]]; then
    return
  fi

  echo $string | grep -q "\["
  if [[ $? -eq 0 ]]; then
    if [[ $LLMDBENCH_CURRENT_STEP == "06" ]]; then
      echo "s^____--^\"\nREPLACE_SPACESC- \"--^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^____^ ^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^\[^- \"^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^\]^\" ^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    fi
    if [[ $LLMDBENCH_CURRENT_STEP == "09" ]]; then
      echo "s^____--^\"\nREPLACE_SPACESC- \"--^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^____^\"\nREPLACE_SPACESC- \"^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^\[^- \"^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^\]^\" ^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    fi
  else
    echo "s^____^ ^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  echo "s^REPLACE_COMMA^,^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands

  for entry in $(echo ${string} | $LLMDBENCH_CONTROL_SCMD -e 's/____/ /g' -e 's^-^\n^g' -e 's^:^\n^g' -e 's^/^\n^g' -e 's^ ^\n^g' -e 's^]^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq); do
    parameter_name=$(echo ${entry} | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_ENV_^\n______^g" -e "s^\"^^g" -e "s^'^^g" | grep "______" | $LLMDBENCH_CONTROL_SCMD -e "s^++++default=.*^^" -e "s^______^^g")
    default_value=$(echo $entry | $LLMDBENCH_CONTROL_SCMD -e "s^++++default=^\n^" | tail -1 | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_ENV_${parameter_name}^^g")
    entry=REPLACE_ENV_${parameter_name}
    value=$(echo ${!parameter_name})
    if [[ -z $value && -z $default_value ]]; then
      announce "❌ ERROR: variable \"$parameter_name\" not defined!"
      exit 1
    fi
    if [[ -z $value && ! -z $default_value ]]; then
      value=$default_value
      echo "s^++++default=$default_value^^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^${entry}^${value}^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    fi
    if [[ ! -z $value && -z $default_value ]]; then
      echo "s^${entry}^${value}^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    fi
    if [[ ! -z $value && ! -z $default_value ]]; then
      echo "s^++++default=$default_value^^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
      echo "s^${entry}^${value}^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    fi
  done
  echo ${string} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  set -euo pipefail
}
export -f render_string

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
  if [[ -x $(command -v openssl) ]]; then
    openssl rand -base64 4 | tr -dc 'a-zA-Z0-9' | tr '[:upper:]' '[:lower:]' | head -c 16
  else
    tr -dc 'a-zA-Z0-9' </dev/urandom | tr '[:upper:]' '[:lower:]' | head -c 16
  fi
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

function run_step {
  local script_name=$1

  local step_nr=$(echo $script_name | cut -d '_' -f 1)

  local script_implementaton=LLMDBENCH_CONTROL_STEP_${step_nr}_IMPLEMENTATION

  if [[ -f $script_name.${!script_implementaton} ]]; then
    local script_path=$script_name.${!script_implementaton}
  else
    local script_path=$(ls ${LLMDBENCH_STEPS_DIR}/${script_name}*.${!script_implementaton})
  fi
  if [ -f $script_path ]; then
    local step_id=$(basename "$script_path")
    export LLMDBENCH_CURRENT_STEP=${step_nr}
    announce "=== Running step: $step_id ==="
    if [[ $LLMDBENCH_CONTROL_DRY_RUN -eq 1 ]]; then
      echo -e "[DRY RUN] $script_path\n"
    fi

    if [[ ${!script_implementaton} == sh ]]; then
      source $script_path
    elif [[ ${!script_implementaton} == py ]]; then
      $LLMDBENCH_CONTROL_PCMD $script_path
      local ec=$?
      if [[ $ec -ne 0 ]]; then
        exit $ec
      fi
    else
      announce "ERROR: Unsupported script type for \"$script_path\""
    fi

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

function add_env_vars_to_pod {
    local varpattern=$1
    varlist=$(env | grep -E "$varpattern" | grep -Ev "LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD|LLMDBENCH_HARNESS_STACK_ENDPOINT_INFO" | cut -d "=" -f 1 | sort | uniq)
    echo "#    "
    for envvar in $varlist; do
      envvalue=${!envvar}
      is_replace=$(echo $envvalue | grep REPLACE_ENV || true)
      if [[ ! -z $is_replace ]]; then
        envvalue=$(echo $envvalue | base64 $LLMDBENCH_BASE64_ARGS)
      fi
      if [[ -f $envvalue ]]; then
        envvalue=$(cat $envvalue | base64 $LLMDBENCH_BASE64_ARGS)
      fi
      is_formatted=$(printf '%s' "$envvalue" | grep "{\\\\s}" || true)
      if [[ ! -z $is_formatted ]]; then
        envvalue=$(printf '%s' "$envvalue" | base64 $LLMDBENCH_BASE64_ARGS)
      fi
      if [[ ! -z ${envvalue} ]]; then
        echo "    - name: ${envvar}"
        echo "      value: \"${envvalue}\"" | $LLMDBENCH_CONTROL_SCMD -e 's^____\"\$^____REPLACE_ENV_^g' -e 's^: ""$^: " "^g' -e 's^""^"^g'
      fi
    done
}
export -f add_env_vars_to_pod

function deploy_harness_config {
    local model=$1
    local modelid=$2
    local local_results_dir=$3
    local local_analysis_dir=$4
    local config=$5

    announce "🚀 Starting ${LLMDBENCH_HARNESS_LOAD_PARALLELISM} pod(s) labeled with \"${LLMDBENCH_HARNESS_POD_LABEL}\" for model \"$model\" ($modelid)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $config" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "✅ ${LLMDBENCH_HARNESS_LOAD_PARALLELISM} pod(s) \"${LLMDBENCH_HARNESS_POD_LABEL}\" for model \"$model\" started"

    announce "⏳ Waiting for ${LLMDBENCH_HARNESS_LOAD_PARALLELISM} pod(s) \"${LLMDBENCH_HARNESS_POD_LABEL}\" for model \"$model\" to be Running (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} wait --for=condition=Ready=True pod -l app=${LLMDBENCH_HARNESS_POD_LABEL} -n ${LLMDBENCH_HARNESS_NAMESPACE} --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "ℹ️ You can follow the execution's output with \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} logs ${LLMDBENCH_HARNESS_POD_LABEL}_<PARALLEL_NUMBER> -f\"..."

    # Identify the shared data-access pod
    LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pod -l role=llm-d-benchmark-data-access --no-headers -o name | $LLMDBENCH_CONTROL_SCMD 's|^pod/||g')

    # Only perform completion checks if debug mode is off and timeout is non-zero
    if [[ $LLMDBENCH_HARNESS_DEBUG -eq 0 && ${LLMDBENCH_HARNESS_WAIT_TIMEOUT} -ne 0 ]]; then
        announce "⏳ Waiting for pods with label \"app=${LLMDBENCH_HARNESS_POD_LABEL}\" to complete (timeout=${LLMDBENCH_HARNESS_WAIT_TIMEOUT}s)..."
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} wait \
            --timeout=${LLMDBENCH_HARNESS_WAIT_TIMEOUT}s --for=condition=ready=False pod -l app=${LLMDBENCH_HARNESS_POD_LABEL}" \
            ${LLMDBENCH_CONTROL_DRY_RUN} \
            ${LLMDBENCH_CONTROL_VERBOSE}
        if ${LLMDBENCH_CONTROL_KCMD} --namespace "${LLMDBENCH_HARNESS_NAMESPACE}" get pods \
                -l "app=${LLMDBENCH_HARNESS_POD_LABEL}" \
                --no-headers | grep -Eq "CrashLoopBackOff|Error|ImagePullBackOff|ErrImagePull"
        then
            announce "❌ Found some pods are in an error state. To list pods \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pods -l app=${LLMDBENCH_HARNESS_POD_LABEL}\""
            exit 1
        fi
        announce "✅ All benchmark pods completed"

        announce "🏗️  Collecting results for pods with label \"app=${LLMDBENCH_HARNESS_POD_LABEL}\"..."
        for i in $(seq 1 "$LLMDBENCH_HARNESS_LOAD_PARALLELISM"); do
            # Per-pod directories
            pod_results_dir="${local_results_dir}_${i}"
            pod_analysis_dir="${local_analysis_dir}_${i}"

            # Path inside the pod for this workload
            _results_dir="${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX}/${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_SUFFIX}_${i}"

            # Copy results from data-access pod to local results directory
            copy_results_cmd="${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} cp --retries=5 \
                ${LLMDBENCH_HARNESS_ACCESS_RESULTS_POD_NAME}:${_results_dir} ${pod_results_dir}"

            # Sync 'analysis' folder to analysis dir and clean up
            copy_analysis_cmd="rsync -az --inplace --delete \
                ${pod_results_dir}/analysis/ ${pod_analysis_dir}/ && rm -rf ${pod_results_dir}/analysis"

            llmdbench_execute_cmd "${copy_results_cmd}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
            if [[ -d ${pod_results_dir}/analysis && $LLMDBENCH_HARNESS_DEBUG -eq 0 && ${LLMDBENCH_HARNESS_WAIT_TIMEOUT} -ne 0 ]]; then
                llmdbench_execute_cmd "$copy_analysis_cmd" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
            fi

            upload_results ${pod_results_dir}
        done
        announce "✅ Collected results for pods with label \"app=${LLMDBENCH_HARNESS_POD_LABEL}\" at: \"${LLMDBENCH_CONTROL_WORK_DIR}/results/\""
        announce "✅ Collected analysis for pods with label \"app=${LLMDBENCH_HARNESS_POD_LABEL}\" at: \"${LLMDBENCH_CONTROL_WORK_DIR}/analysis/\""

        announce "🗑️ Deleting pods with label \"app=${LLMDBENCH_HARNESS_POD_LABEL}\" for model \"$model\" ..."
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod -l function=load_generator" \
            ${LLMDBENCH_CONTROL_DRY_RUN} \
            ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ Pods with label \"app=${LLMDBENCH_HARNESS_POD_LABEL}\" for model \"$model\" deleted"

    elif [[ $LLMDBENCH_HARNESS_WAIT_TIMEOUT -eq 0 ]]; then
      announce "ℹ️ Harness was started with LLMDBENCH_HARNESS_WAIT_TIMEOUT=0. Will NOT wait for pod \"${LLMDBENCH_HARNESS_POD_LABEL}\" for model \"$model\" to be in \"Completed\" state. The pod can be accessed through \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} exec -it pod/<POD_NAME> -- bash\""
      announce "ℹ️ To list pod names \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pods -l app=${LLMDBENCH_HARNESS_POD_LABEL}\""
    else
      announce "ℹ️ Harness was started in \"debug mode\". The pod can be accessed through \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} exec -it pod/<POD_NAME> -- bash\""
      announce "ℹ️ To list pod names \"${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pods -l app=${LLMDBENCH_HARNESS_POD_LABEL}\""
      announce "ℹ️ In order to execute a given workload profile, run \"llm-d-benchmark.sh -l <[$(get_harness_list)]> -w [WORKLOAD FILE NAME]\" (all inside the pod <POD_NAME>)"
    fi

    return 0
}
export -f deploy_harness_config

function capture_pod_logs {
    local model=$1
    local local_results_dir=$2

    local modelid_label=$(model_attribute $model modelid_label)
    for i in $(seq 1 "$LLMDBENCH_HARNESS_LOAD_PARALLELISM"); do
      pod_results_dir="${local_results_dir}_${i}"
      pod_analysis_dir="${local_analysis_dir}_${i}"

      announce "🏗️ Capturing the current status of all pods in namespace \"$LLMDBENCH_VLLM_COMMON_NAMESPACE\" to ${pod_results_dir}/pod_status.txt ..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE get pods -o wide > ${pod_results_dir}/pod_status.txt" \
      ${LLMDBENCH_CONTROL_DRY_RUN} \
      ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Pod status captured to \"${pod_results_dir}/pod_status.txt\""

      announce "🏗️ Capturing logs for all pods in namespace \"$LLMDBENCH_VLLM_COMMON_NAMESPACE\" to ${pod_results_dir}/logs/ ..."
      mkdir -p ${pod_results_dir}/logs/
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE logs --tail=-1 --prefix=true -l llm-d.ai/model=\"$modelid_label\" > ${pod_results_dir}/logs/modelserving_pods.log"  \
      ${LLMDBENCH_CONTROL_DRY_RUN} \
      ${LLMDBENCH_CONTROL_VERBOSE}

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE logs --tail=-1 --prefix=true -l inferencepool=\"${modelid_label}-gaie-epp\" > ${pod_results_dir}/logs/epp_pods.log"  \
      ${LLMDBENCH_CONTROL_DRY_RUN} \
      ${LLMDBENCH_CONTROL_VERBOSE}

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace $LLMDBENCH_VLLM_COMMON_NAMESPACE logs --tail=-1 --prefix=true -l \"app.kubernetes.io/component=inference-gateway\" > ${pod_results_dir}/logs/igw_pods.log"  \
      ${LLMDBENCH_CONTROL_DRY_RUN} \
      ${LLMDBENCH_CONTROL_VERBOSE}
      announce "✅ Pod logs captured to \"${pod_results_dir}/logs/\""
    done
}
export -f capture_pod_logs

function create_harness_pod {

  local _podname=$1
  local _work_dir=$2

  is_pvc=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pvc --ignore-not-found | grep ${LLMDBENCH_HARNESS_PVC_NAME} || true)
  if [[ -z ${is_pvc} ]]; then
      announce "❌ PVC \"${LLMDBENCH_HARNESS_PVC_NAME}\" not created on namespace \"${LLMDBENCH_HARNESS_NAMESPACE}\" unable to continue"
      exit 1
  fi

  is_cfgmap=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get configmap --ignore-not-found | grep llm-d-benchmark-standup-parameters || true)

  # Sanitize the stack name to make it a valid k8s/OpenShift resource name
  local LLMDBENCH_HARNESS_SANITIZED_STACK_NAME=$(echo "${LLMDBENCH_HARNESS_STACK_NAME}" | $LLMDBENCH_CONTROL_SCMD 's|[/:]|-|g')
  mkdir -p "${_work_dir}/setup/yamls"
  cat <<EOF > $_work_dir/setup/yamls/pod_benchmark-launcher.yaml
apiVersion: v1
kind: Pod
metadata:
  name: ${_podname}
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
  labels:
    app: ${LLMDBENCH_HARNESS_POD_LABEL}
    function: load_generator
spec:
  containers:
  - name: harness
    image: $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG})
    imagePullPolicy: Always
    securityContext:
      runAsUser: 0
    command: ["sh", "-c"]
    args:
    - "${LLMDBENCH_HARNESS_EXECUTABLE}"
    resources:
      limits:
        cpu: "${LLMDBENCH_HARNESS_CPU_NR}"
        memory: ${LLMDBENCH_HARNESS_CPU_MEM}
      requests:
        cpu: "${LLMDBENCH_HARNESS_CPU_NR}"
        memory: ${LLMDBENCH_HARNESS_CPU_MEM}
    env:
    - name: LLMDBENCH_RUN_EXPERIMENT_LAUNCHER
      value: "1"
    - name: LLMDBENCH_RUN_WORKSPACE_DIR
      value: "$LLMDBENCH_RUN_WORKSPACE_DIR"
    - name: LLMDBENCH_CONTROL_WORK_DIR
      value: "${LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR}"
    - name: LLMDBENCH_MAGIC_ENVAR
      value: "harness_pod"
    $(add_env_vars_to_pod $LLMDBENCH_CONTROL_ENV_VAR_LIST_TO_POD)
    - name: HF_TOKEN_SECRET
      value: "${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}"
    - name: HUGGING_FACE_HUB_TOKEN
      valueFrom:
        secretKeyRef:
          name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
          key: HF_TOKEN
    - name: POD_NAME
      valueFrom:
        fieldRef:
          fieldPath: metadata.name
    - name: POD_UID
      valueFrom:
        fieldRef:
          fieldPath: metadata.uid
    volumeMounts:
    - name: results
      mountPath: /requests
EOF

  if [[ ! -z $is_cfgmap ]]; then
    cat <<EOF >> $_work_dir/setup/yamls/pod_benchmark-launcher.yaml
    - name: standup
      mountPath: /standup
EOF
  fi

  for profile_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
    cat <<EOF >> $_work_dir/setup/yamls/pod_benchmark-launcher.yaml
    - name: ${profile_type}-profiles
      mountPath: /workspace/profiles/${profile_type}
EOF
  done
  cat <<EOF >> $_work_dir/setup/yamls/pod_benchmark-launcher.yaml
  volumes:
  - name: results
    persistentVolumeClaim:
      claimName: $LLMDBENCH_HARNESS_PVC_NAME
EOF
  for profile_type in ${LLMDBENCH_HARNESS_PROFILE_HARNESS_LIST}; do
    cat <<EOF >> $_work_dir/setup/yamls/pod_benchmark-launcher.yaml
  - name: ${profile_type}-profiles
    configMap:
      name: ${profile_type}-profiles
EOF
  done

  if [[ ! -z $is_cfgmap ]]; then
    cat <<EOF >> $_work_dir/setup/yamls/pod_benchmark-launcher.yaml
  - name: standup
    configMap:
      name: llm-d-benchmark-standup-parameters
  restartPolicy: Never
EOF
  fi
}
export -f create_harness_pod

function get_model_name_from_pod {
    local namespace=$1
    local image=$2
    local url=$3
    local port=$4

    has_protocol=$(echo $url | grep "http://" || true)
    if [[ -z $has_protocol ]]; then
        local url="http://$url"
    fi
    # Check if the URL already contains a port number.
    # If not, append the default port provided.
    if ! echo "$url" | grep -q ':[0-9]'; then
        url="$url:$port"
    fi
    # --- END: Corrected Port Logic ---

    local url=$url/v1/models
    local response=$(llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} run testinference-pod-$(get_rand_string) -n $namespace --attach --restart=Never --rm --image=$image --quiet --command -- bash -c \"curl --no-progress-meter $url\"" ${LLMDBENCH_CONTROL_DRY_RUN} 0 0 2 0)
    is_jq=$(echo $response | jq -r . || true)

    if [[ -z $is_jq ]]; then
        return 1
    fi
    has_model=$(echo "$is_jq" | jq -r ".data[].id" || true)
    if [[ -z $has_model ]]; then
        return 1
    fi
    echo $has_model
}
export -f get_model_name_from_pod

function render_workload_templates {
    local workload=${1:-all}

    local workload=$(echo $workload | $LLMDBENCH_CONTROL_SCMD 's^\.yaml^^g' )
    if [[ $workload == "all" ]]; then
      workload_template_list=$(find ${LLMDBENCH_HARNESS_PROFILES_DIR}/ -name "*.yaml.in")
    else
      workload_template_list=$(find ${LLMDBENCH_HARNESS_PROFILES_DIR}/ -name "${workload}.yaml.in")
    fi

    rm -f $LLMDBENCH_CONTROL_WORK_DIR/workload/profiles/overrides.txt
    touch $LLMDBENCH_CONTROL_WORK_DIR/workload/profiles/overrides.txt
    if [[ ! -z $LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES ]]; then
      for entry in $(echo $LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES | $LLMDBENCH_CONTROL_SCMD 's^,^ ^g'); do
        parm=$(echo $entry | cut -d '=' -f 1)
        val=$(echo $entry | cut -d '=' -f 2)
        echo "s^$parm:.*^$parm: $val^g" >> $LLMDBENCH_CONTROL_WORK_DIR/workload/profiles/overrides.txt
      done
    fi

    announce "🛠️ Rendering \"$workload\" workload profile templates under \"${LLMDBENCH_HARNESS_PROFILES_DIR}\"..."
    for workload_template_full_path in $workload_template_list; do
      workload_template_type=$(echo ${workload_template_full_path} | rev | cut -d '/' -f 2 | rev)
      workload_template_file_name=$(echo ${workload_template_full_path} | rev | cut -d '/' -f 1 | rev | $LLMDBENCH_CONTROL_SCMD -e "s^\.yaml.in$^^g")
      workload_output_file=${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$workload_template_type/$workload_template_file_name
      mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$workload_template_type/
      treatment_list_dir=${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$workload_template_type/treatment_list
      if [[ -d $treatment_list_dir ]]; then
        for treatment in $(ls $treatment_list_dir); do
            workload_output_file_suffix=$(echo ${treatment} | cut -d '.' -f 1)
            render_template $workload_template_full_path ${workload_output_file}_${workload_output_file_suffix}.yaml ${treatment_list_dir}/$treatment 0 0
        done
      else
        render_template $workload_template_full_path $workload_output_file.yaml $LLMDBENCH_CONTROL_WORK_DIR/workload/profiles/overrides.txt 0 0
      fi
    done
    announce "✅ Done rendering \"$workload\" workload profile templates to \"${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/\""
}
export -f render_workload_templates

function generate_standup_parameter_scenarios {
  local scenario_dir=$1
  local scenario_file=$2
  local standup_parameter_file=${3:-}

  local output_dir=${scenario_dir}/setup/treatment_list
  rm -rf $output_dir
  mkdir -p $output_dir

  if [[ -z $standup_parameter_file || ! -s $standup_parameter_file || $(cat $standup_parameter_file | yq -r .setup.treatments) == "null" ]]; then
    cp -f $scenario_file ${scenario_dir}/setup/treatment_list/treatment_none.sh
    return 0
  fi

  mkdir -p ${scenario_dir}/setup/experiments/
  cp -f $standup_parameter_file ${LLMDBENCH_CONTROL_WORK_DIR}/setup/experiments/

  if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE -eq 0 && $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 0 ]]; then
    touch $output_dir/treatment_run_only.sh
    return
  fi

  cat $standup_parameter_file | yq -r .setup.treatments | while IFS=: read -r name treatment; do
    if [ -z "$treatment" ]; then  # handle list without keys
      treatment=$(yq .[] <<<"$name")
      local name=setup_${treatment//,/_}
    fi
    local name=$($LLMDBENCH_CONTROL_SCMD -e 's/[^[:alnum:]][^[:alnum:]]*/_/g' <<<"${name}")   # remove non alphanumeric
    cat $scenario_file > $output_dir/treatment_$name.sh
    $LLMDBENCH_CONTROL_SCMD -i "1i#treatment_$name"  $output_dir/treatment_$name.sh
    local j=1
    for value in $(echo $treatment | $LLMDBENCH_CONTROL_SCMD 's/,/ /g'); do
      local param=$(cat $standup_parameter_file | yq -r ".setup.factors[$(($j - 1))]")
      local has_param=$(cat $output_dir/treatment_$name.sh | grep "$param=" || true)
      if [[ -z $has_param ]]; then
        echo "export $param=$value" >> $output_dir/treatment_$name.sh
      else
        $LLMDBENCH_CONTROL_SCMD -i "s^.*$param=.*^export $param=$value^g"  $output_dir/treatment_$name.sh
      fi
      $LLMDBENCH_CONTROL_SCMD -i "s^REPLACE_TREATMENT_NR^treatment_$name^g"  $output_dir/treatment_$name.sh
      $LLMDBENCH_CONTROL_SCMD -i "s^_treatment_nr^treatment_$name^g"  $output_dir/treatment_$name.sh
      j=$((j+1))
    done
    for parvar in $(cat $standup_parameter_file | yq -r '.setup.constants[]' | $LLMDBENCH_CONTROL_SCMD 's^: ^_____^g')
    do
      local param=$(echo $parvar | $LLMDBENCH_CONTROL_SCMD 's^_____^ ^g' | cut -d ' ' -f 1)
      local value=$(echo $parvar | $LLMDBENCH_CONTROL_SCMD 's^_____^ ^g' | cut -d ' ' -f 2)
      local has_param=$(cat $output_dir/treatment_$name.sh | grep "$param=" || true)
      if [[ -z $has_param ]]; then
        echo "export $param=$value" >> $output_dir/treatment_$name.sh
      else
        $LLMDBENCH_CONTROL_SCMD -i "s^.*$param=.*^export $param=$value^g"  $output_dir/treatment_$name.sh
      fi
    done
  done
}
export -f generate_standup_parameter_scenarios

function generate_profile_parameter_treatments {
  local harness_name=$1
  local run_parameter_file=${2:-}

  if [[ -z $run_parameter_file ]]; then
    return 0
  fi

  local output_dir=${LLMDBENCH_CONTROL_WORK_DIR}/workload/profiles/$harness_name/treatment_list

  rm -rf $output_dir
  mkdir -p $output_dir

  cp -f $run_parameter_file ${LLMDBENCH_CONTROL_WORK_DIR}/workload/experiments/

  cat $run_parameter_file | yq -r .run.treatments | while IFS=: read -r name treatment; do
    if [ -z "$treatment" ]; then  # handle list without keys
      treatment=$(yq .[] <<<"$name")
      name=run_${treatment//,/_}
    fi
    name=$(sed -e 's/[^[:alnum:]][^[:alnum:]]*/_/g' <<<"${name}")   # remove non alphanumeric
    echo "1i#treatment_${name}.txt" >> $output_dir/treatment_${name}.txt
    local j=1
    for value in $(echo $treatment | $LLMDBENCH_CONTROL_SCMD 's/,/ /g'); do
      local value=$(echo "$value" | $LLMDBENCH_CONTROL_SCMD 's^"^^g' | $LLMDBENCH_CONTROL_SCMD -e "s^REPLACE_COMMA^,^g")
      local param=$(cat $run_parameter_file | yq -r ".run.factors[$(($j - 1))]")
      echo "s^$param: .*^$param: $value^g" >> $output_dir/treatment_${name}.txt
      j=$((j+1))
    done

    for parvar in $(cat $run_parameter_file | yq -r '.run.constants[]' | $LLMDBENCH_CONTROL_SCMD 's^: ^_____^g')
    do
      local cparam=$(echo $parvar | $LLMDBENCH_CONTROL_SCMD 's^_____^ ^g' | cut -d ' ' -f 1)
      local cvalue=$(echo $parvar | $LLMDBENCH_CONTROL_SCMD 's^_____^ ^g' | cut -d ' ' -f 2)
      echo "s^$cparam:.*^$cparam: $cvalue^g" >> $output_dir/treatment_${name}.txt
    done

    if [[ ! -z $LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES ]]; then
      for entry in $(echo $LLMDBENCH_HARNESS_EXPERIMENT_PROFILE_OVERRIDES | $LLMDBENCH_CONTROL_SCMD 's^,^ ^g'); do
        local parm=$(echo $entry | cut -d '=' -f 1)
        local val=$(echo $entry | cut -d '=' -f 2)
        echo "s^$parm:.*^$parm: $val^g" >> $output_dir/treatment_${name}.txt
      done
    fi
  done
}
export -f generate_profile_parameter_treatments

function cleanup_pre_execution {
  announce "🗑️ Deleting pods with label \"${LLMDBENCH_HARNESS_POD_LABEL}\"..."
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete pod -l app=${LLMDBENCH_HARNESS_POD_LABEL},function=load_generator --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  # Sanitize the stack name to make it a valid K8s/OpenShift resource name
  local LLMDBENCH_HARNESS_SANITIZED_STACK_NAME=$(echo "${LLMDBENCH_HARNESS_STACK_NAME}" | $LLMDBENCH_CONTROL_SCMD 's|[/:]|-|g')
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} delete job lmbenchmark-evaluate-${LLMDBENCH_HARNESS_SANITIZED_STACK_NAME} --ignore-not-found" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  announce "ℹ️ Done deleting pods with label \"${LLMDBENCH_HARNESS_POD_LABEL}\" (it will be now recreated)"

}
export -f cleanup_pre_execution

function validate_model_name {
  local _model_name=$1
  for mparm in model parameters majorversion kind modelid_label; do
    if [[ -z $(model_attribute ${_model_name} ${mparm}) ]]; then
      announce "❌ Invalid model name \"${_model_name}\""
      exit 1
    fi
  done
}
export -f validate_model_name

function backup_work_dir {
  local backup_suffix=${1:-"auto"}
  local unconditional=${2:-0}

  if [[ $backup_suffix == "auto" ]]; then
    backup_suffix=$(date +"%Y-%m-%d_%H.%M.%S")
  fi

  local backup=0

  local backup_target=$(echo $LLMDBENCH_CONTROL_WORK_DIR | $LLMDBENCH_CONTROL_SCMD -e 's^//^/^g' -e 's^/$^^').$backup_suffix

  if [[ $unconditional -eq 1 ]];
  then
    announce "ℹ️  Unconditionally moving \"$LLMDBENCH_CONTROL_WORK_DIR\" to \"$backup_target\"..."
    local backup=1
  else
    if [[ $LLMDBENCH_CONTROL_WORK_DIR_BACKEDUP -eq 1 ]]; then
      return 0
    fi

    if [[ $LLMDBENCH_CONTROL_WORK_DIR_SET -eq 1 && $LLMDBENCH_CONTROL_STANDUP_ALL_STEPS -eq 1 ]]; then
      if [[ $LLMDBENCH_CURRENT_STEP == "00" && ${LLMDBENCH_CONTROL_CALLER} != "standup.sh" || $LLMDBENCH_CURRENT_STEP == "00" && ${LLMDBENCH_CONTROL_CALLER} != "e2s.sh" ]]; then
        if [[ $(ls $LLMDBENCH_CONTROL_WORK_DIR/setup/commands | wc -l) -ne 0 ]]; then
          announce "🗑️  Environment Variable \"LLMDBENCH_CONTROL_WORK_DIR\" was set outside \"setup/env.sh\", all steps were selected on \"setup/standup.sh\" and this is the first step on standup. Moving \"$LLMDBENCH_CONTROL_WORK_DIR\" to \"$backup_target\"..."
        fi
        local backup=1
      fi
    fi
  fi

  if [[ $backup -eq 1 ]]; then
    # Do not use "llmdbench_execute_cmd" for these commands. Those need to executed even on "dry-run"
    if [[ -d $backup_target ]]; then
      rsync -a --inplace --delete $LLMDBENCH_CONTROL_WORK_DIR/ $backup_target/
    else
      mv -f $LLMDBENCH_CONTROL_WORK_DIR/ $backup_target/
    fi

    export LLMDBENCH_CONTROL_WORK_DIR_BACKEDUP=1
    prepare_work_dir
    if [[ -f $backup_target/environment/context.ctx ]]; then
      # Do not use "llmdbench_execute_cmd" for these commands. Those need to executed even on "dry-run"
      cp -f $backup_target/environment/context.ctx $LLMDBENCH_CONTROL_WORK_DIR/environment/context.ctx
    fi
    echo
  fi
}
export -f backup_work_dir

# Check if a Hugging Face model is gated (requires manual approval)
# Usage: is_hf_model_gated <model_id>
# Example: is_hf_model_gated ibm-granite granite-3.1-8b-instruct
function is_hf_model_gated {
    local model_id="$1"
    local url="https://huggingface.co/api/models/${model_id}"

    local response=$(curl -s -H "Accept: application/json" "${url}")
    if [[ $? -ne 0 || -z "${response}" ]]; then
        return 2
    fi

    local gated=$(echo "${response}" | jq -r '.gated // false')
    if [[ ${gated} == "false" ]]; then
      return 1
    else
      return 0
    fi
}
export -f is_hf_model_gated

# Check if a Hugging Face user (via token) has access to a model
# Usage: user_has_hf_model_access <model_id> <hf_token>
# Example: user_has_hf_model_access ibm-granite granite-3.1-8b-instruct $HF_TOKEN
function user_has_hf_model_access {
    local model_id="$1"
    local hf_token="$2"
    local url="https://huggingface.co/${model_id}/resolve/main/config.json"

    local http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${hf_token}" \
        -L "${url}")

    case "$http_code" in 200) return 0 ;; 401|403) return 1 ;; *) return 2 ;; esac
}
export -f user_has_hf_model_access

function verify_output_destination {
  local _output_destination=$1
  if [[ ${_output_destination} == "local" ]]; then
    export LLMDBENCH_HARNESS_OUTPUT_STORAGE_TYPE=local
    return 0
  else
    if [[ "${_output_destination}" == *"://"* ]]; then
      _storage_type="cloud"
      _scheme=$(echo "${_output_destination}" | cut -d: -f1)
      _bucket=$(echo "${_output_destination}" | cut -d ':' -f 2 | $LLMDBENCH_CONTROL_SCMD -e 's^//^^g' -e 's:/*$::')
      case "${_scheme}" in
        gs)
          export LLMDBENCH_HARNESS_OUTPUT_STORAGE_TYPE=gs
          announce "ℹ️  Verifying GCS output destination..."
          if ! command -v gcloud &> /dev/null; then
            announce "❌ 'gcloud' command not found, but is required for 'gs://' output."
            exit 1
          else
            is_bucket=$(gcloud storage buckets list | grep ${_bucket} || true)
          fi
          ;;
        s3)
          export LLMDBENCH_HARNESS_OUTPUT_STORAGE_TYPE=s3
          announce "ℹ️  Verifying S3 output destination..."
          if ! command -v aws &> /dev/null; then
            announce "❌ 'aws' command not found, but is required for 's3://' output."
            exit 1
          else
            is_bucket=$(aws s3 ls | grep ${_bucket} || true)
          fi
          ;;
        *)
          announce "❌ ERROR: Unsupported cloud provider scheme '${_scheme}' for destination '${_output_destination}'."
          exit 1
          ;;
      esac

      if [[ -z $is_bucket ]]; then
        announce "❌ ERROR: Bucket \"${_bucket}\" ('${_output_destination}') not found."
        exit1 1
      else
        announce "✅ Output destination verified\""
      fi
    fi
  fi
}
export -f verify_output_destination

function upload_results {
  local local_results_dir=$1
  local remote_results_dir=$(echo $local_results_dir | $LLMDBENCH_CONTROL_SCMD -e "s^$LLMDBENCH_CONTROL_WORK_DIR/results/^^g")

  if [[ "${LLMDBENCH_HARNESS_OUTPUT_STORAGE_TYPE}" == "local" ]]; then
    return 0
  fi
  case ${LLMDBENCH_HARNESS_OUTPUT_STORAGE_TYPE} in
    gs)
      announce "☁️  Uploading results to GCS bucket ${LLMDBENCH_HARNESS_OUTPUT}"
      gcloud storage cp --recursive "${local_results_dir}/" "${LLMDBENCH_HARNESS_OUTPUT}/${remote_results_dir}/"
      ;;
    s3)
      announce "☁️  Uploading results to S3 bucket ${LLMDBENCH_HARNESS_OUTPUT}"
      aws s3 cp --recursive "${local_results_dir}/" "${LLMDBENCH_HARNESS_OUTPUT}/${remote_results_dir}/"
      ;;
    local)
      announce "ℹ️ Results saved to local folder."
      ;;
    *)
      announce "❌ ERROR: unknown or unsupported storage provider \"${LLMDBENCH_HARNESS_OUTPUT_STORAGE_TYPE}\"."
      exit 1
      ;;
  esac
}

function render_template {
  local template_file_path=$1
  local output_file_path=${2:-"none"}
  local additional_replace_commands=${3:-"none"}
  local cmdline_mode=${4:-0}
  local env_var_mode=${5:-0}

  rm -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  touch $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands

  if [[ $additional_replace_commands != "none" ]]; then
    cat $additional_replace_commands >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  for entry in $(cat ${template_file_path} | grep -v ^# | $LLMDBENCH_CONTROL_SCMD -e 's^-^\n^g' -e 's^:^\n^g' -e 's^ ^\n^g' -e 's^ ^^g' -e 's^\.^\n^g' -e 's^\/^\n^g' | grep -E "REPLACE_ENV" | uniq); do
    render_string $entry &>/dev/null
  done

  echo "s^#.*^^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  if [[ $cmdline_mode -eq 1 ]]; then
    if [[ $LLMDBENCH_CURRENT_STEP == "06" ]]; then
      echo "  - |"
      local spacec=$(printf '%*s' 12 '')
    fi

    if [[ $LLMDBENCH_CURRENT_STEP == "09" ]]; then
      echo "- |"
      local spacec=$(printf '%*s' 8 '')
    fi
    echo "s^REPLACE_SPACESC^$spacec^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    echo "s^ --^\\n$spacec--^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    echo "s^\\n^ \\\\\n^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
    echo "s^REPLACE_COMMA^,^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  if [[ $env_var_mode -eq 1 ]]; then
    if [[ $LLMDBENCH_CURRENT_STEP == "06" ]]; then
      local spacec=$(printf '%*s' 8 '')
    fi
    if [[ $LLMDBENCH_CURRENT_STEP == "09" ]]; then
      local spacec=$(printf '%*s' 6 '')
    fi
    echo "s^REPLACE_SPACESC^$spacec^g" >> $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  if [[ $output_file_path != "none" ]]; then
    cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands > $output_file_path
  fi

  if [[ $cmdline_mode -eq 1 ]]; then
    echo "REPLACE_SPACESC$(cat ${template_file_path})" | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi

  if [[ $env_var_mode -eq 1 ]]; then
    echo "$(cat ${template_file_path} | $LLMDBENCH_CONTROL_SCMD -e 's^\^^REPLACE_SPACESC^g')" | $LLMDBENCH_CONTROL_SCMD -e '1s^REPLACE_SPACESC^^' | $LLMDBENCH_CONTROL_SCMD -f $LLMDBENCH_CONTROL_WORK_DIR/setup/sed-commands
  fi
}
export -f render_template
