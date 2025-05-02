# Shared configuration and validation
export OPENSHIFT_HOST="${OPENSHIFT_HOST:-https://api.fmaas-vllm-d.fmaas.res.ibm.com}"
export OPENSHIFT_TOKEN="${OPENSHIFT_TOKEN:-sha256~sVYh-xxx}"
export OPENSHIFT_NAMESPACE="${OPENSHIFT_NAMESPACE:-}"
export HF_TOKEN="${HF_TOKEN:-}"
export QUAY_USER="${QUAY_USER:-}"
export QUAY_PASSWORD="${QUAY_PASSWORD:-}"
export DOCKER_EMAIL="${DOCKER_EMAIL:-your@email.address}"
export STORAGE_CLASS="${STORAGE_CLASS:-ocs-storagecluster-cephfs}"
export GIT_REPO="${GIT_REPO:-https://github.com/wangchen615/fmperf.git}"
export GIT_BRANCH="${GIT_BRANCH:-dev-lmbenchmark}"
export FMPERF_EXAMPLE="${FMPERF_EXAMPLE:-examples/example_llm-d-lmbenchmark-openshift.py}"
export CONDA_ENV_NAME="${CONDA_ENV_NAME:-fmperf-env}"
export MODEL_CACHE_SIZE="${MODEL_CACHE_SIZE:-300Gi}"
export MODEL_IMAGE="vllm/vllm-openai:latest"

required_vars=("OPENSHIFT_NAMESPACE" "HF_TOKEN" "QUAY_USER" "QUAY_PASSWORD")
for var in "${required_vars[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "❌ Environment variable '$var' is not set."
    exit 1
  fi
done
