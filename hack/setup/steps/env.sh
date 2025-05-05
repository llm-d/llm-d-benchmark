# Shared configuration and validation
export LLMDBENCH_OPENSHIFT_HOST="${LLMDBENCH_OPENSHIFT_HOST:-https://api.fmaas-vllm-d.fmaas.res.ibm.com}"
export LLMDBENCH_OPENSHIFT_TOKEN="${LLMDBENCH_OPENSHIFT_TOKEN:-sha256~sVYh-xxx}"
export LLMDBENCH_OPENSHIFT_NAMESPACE="${LLMDBENCH_OPENSHIFT_NAMESPACE:-}"
export LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT="${LLMDBENCH_OPENSHIFT_SERVICE_ACCOUNT:-default}"
export LLMDBENCH_HF_TOKEN="${LLMDBENCH_HF_TOKEN:-}"
export LLMDBENCH_QUAY_USER="${LLMDBENCH_QUAY_USER:-}"
export LLMDBENCH_QUAY_PASSWORD="${LLMDBENCH_QUAY_PASSWORD:-}"
export LLMDBENCH_DOCKER_EMAIL="${LLMDBENCH_DOCKER_EMAIL:-your@email.address}"
export LLMDBENCH_STORAGE_CLASS="${LLMDBENCH_STORAGE_CLASS:-ocs-storagecluster-cephfs}"
export LLMDBENCH_FMPERF_GIT_REPO="${LLMDBENCH_FMPERF_GIT_REPO:-https://github.com/wangchen615/fmperf.git}"
export LLMDBENCH_FMPERF_DIR="${LLMDBENCH_FMPERF_DIR:-/tmp}"
export LLMDBENCH_FMPERF_GIT_BRANCH="${LLMDBENCH_FMPERF_GIT_BRANCH:-dev-lmbenchmark}"
export LLMDBENCH_FMPERF_EXPERIMENT_LIST="${LLMDBENCH_FMPERF_EXPERIMENT_LIST:-examples/example_llm-d-lmbenchmark-openshift.py}"
export LLMDBENCH_KVCM_DIR="${LLMDBENCH_KVCM_DIR:-/tmp}"
export LLMDBENCH_GAIE_DIR="${LLMDBENCH_GAIE_DIR:-/tmp}"
export LLMDBENCH_CONDA_ENV_NAME="${LLMDBENCH_CONDA_ENV_NAME:-fmperf-env}"
export LLMDBENCH_MODEL_LIST=${LLMDBENCH_MODEL_LIST:-llama-8b,llama-70b}
export LLMDBENCH_GPU_MODEL=${LLMDBENCH_GPU_MODEL:-NVIDIA-A100-SXM4-80GB}
export LLMDBENCH_VLLM_REPLICAS=${LLMDBENCH_VLLM_REPLICAS:-1}
export LLMDBENCH_VLLM_PERSISTENCE_ENABLED=${LLMDBENCH_VLLM_PERSISTENCE_ENABLED:-false}
export LLMDBENCH_EPP_IMAGE=${LLMDBENCH_EPP_IMAGE:-quay.io/vmaroon/gateway-api-inference-extension/epp:kvc-v3}
export LLMDBENCH_MODEL_CACHE_SIZE="${LLMDBENCH_MODEL_CACHE_SIZE:-300Gi}"
export LLMDBENCH_MODEL_IMAGE="vllm/vllm-openai:latest"

export LLMDBENCH_OPENSHIFT_CLUSTER_NAME=$(echo ${LLMDBENCH_OPENSHIFT_HOST} | cut -d '.' -f 2)

if [[ -f ${HOME}/.kube/config-${LLMDBENCH_OPENSHIFT_CLUSTER_NAME} ]]
then
  export LLMDBENCH_KCMD="oc --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_OPENSHIFT_CLUSTER_NAME}"
  export LLMDBENCH_HCMD="helm --kubeconfig ${HOME}/.kube/config-${LLMDBENCH_OPENSHIFT_CLUSTER_NAME}"
else
  export LLMDBENCH_KCMD="oc"
  export LLMDBENCH_HCMD="helm"

  ${LLMDBENCH_KCMD} login --token="${LLMDBENCH_OPENSHIFT_TOKEN}" --server="${LLMDBENCH_OPENSHIFT_HOST}:6443"
fi

export LLMDBENCH_HOST_SHELL=${SHELL:5}

uname -s | grep -qi darwin
if [[ $? -eq 0 ]]
then
    export LLMDBENCH_HOST_OS=mac
else
    export LLMDBENCH_HOST_OS=linux
fi

which gsed > /dev/null 2>&1
if [[ $? -eq 0 ]]
then
    export LLMDBENCH_SCMD=gsed
else
    export LLMDBENCH_SCMD=sed
fi

export LLMDBENCH_PCMD=${LLMDBENCH_PCMD:-python3}

declare -A LLMDBENCH_MODEL2PARAM
#LLMDBENCH_MODEL2PARAM["llama-8b:label"]="llama-2-8b"
#LLMDBENCH_MODEL2PARAM["llama-8b:name"]="meta-llama/Llama-2-8b-chat-hf"
LLMDBENCH_MODEL2PARAM["llama-8b:label"]="llama-3-8b"
LLMDBENCH_MODEL2PARAM["llama-8b:name"]="meta-llama/Llama-3.1-8B-Instruct"
LLMDBENCH_MODEL2PARAM["llama-8b:params"]="8b"
LLMDBENCH_MODEL2PARAM["llama-70b:label"]="llama-3-70b"
LLMDBENCH_MODEL2PARAM["llama-70b:name"]="meta-llama/Llama-3.1-70B-Instruct"
LLMDBENCH_MODEL2PARAM["llama-70b:params"]="70b"

required_vars=("LLMDBENCH_OPENSHIFT_NAMESPACE" "LLMDBENCH_HF_TOKEN" "LLMDBENCH_QUAY_USER" "LLMDBENCH_QUAY_PASSWORD")
for var in "${required_vars[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "❌ Environment variable '$var' is not set."
    exit 1
  fi
done
