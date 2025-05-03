# Deploy and Teardown benchmark tests

### first export your vars
```
export LLMDBENCH_OPENSHIFT_HOST="https://api.fmaas-platform-eval.fmaas.res.ibm.com"
export OPENSHIFT_TOKEN="..."
export OPENSHIFT_NAMESPACE="..."
export LLMDBENCH_HF_TOKEN="..."
export LLMDBENCH_QUAY_USER="..."
export LLMDBENCH_QUAY_PASSWORD="..."
export LLMDBENCH_DOCKER_EMAIL="your@email.com"
export LLMDBENCH_STORAGE_CLASS="ocs-storagecluster-cephfs"
export LLMDBENCH_FMPERF_GIT_REPO="https://github.com/wangchen615/fmperf.git"
export LLMDBENCH_FMPERF_GIT_BRANCH="dev-lmbenchmark"
export LLMDBENCH_CONDA_ENV_NAME="fmperf-env"
export LLMDBENCH_MODEL_CACHE_SIZE="300Gi"
export LLMDBENCH_MODEL_IMAGE="vllm/vllm-openai:latest"
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

## to execute an individual step (full name or number)
```
./deploy.sh --step 09_run_experiment.sh
./deploy.sh --step 09
```