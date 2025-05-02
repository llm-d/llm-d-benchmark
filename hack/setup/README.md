# Deploy and Teardown benchmark tests

### first export your vars
```
export OPENSHIFT_HOST="https://api.fmaas-platform-eval.fmaas.res.ibm.com"
export OPENSHIFT_TOKEN="..."
export OPENSHIFT_NAMESPACE="..."
export HF_TOKEN="..."
export QUAY_USER="..."
export QUAY_PASSWORD="..."
export DOCKER_EMAIL="your@email.com"
export STORAGE_CLASS="ocs-storagecluster-cephfs"
export GIT_REPO="https://github.com/wangchen615/fmperf.git"
export GIT_BRANCH="dev-lmbenchmark"
export FMPERF_EXAMPLE="examples/example_llm-d-lmbenchmark-openshift.py"
export CONDA_ENV_NAME="fmperf-env"
export MODEL_CACHE_SIZE="300Gi"
export MODEL_IMAGE="vllm/vllm-openai:latest"
```

```
git clone https://github.com/neuralmagic/llm-d-benchmark
cd llm-d-benchmark/hack/setup
```

## to dry-run
```
./deploy.sh --dry-run
```

## to deploy and test
```
./deploy.sh
```

## to cleanup your mess
```
./cleanup.sh
```

## to execute an individual step
```
./deploy.sh --step 09_run_experiment.sh
```
