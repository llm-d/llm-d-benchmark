# Deploy and Teardown benchmark tests

## Clone llm-d-benchmark repo
```
git clone https://github.com/neuralmagic/llm-d-benchmark
cd llm-d-benchmark/hack/setup
```

## Set the minimalistic
```
export LLMDBENCH_OPENSHIFT_HOST="https://api.fmaas-platform-eval.fmaas.res.ibm.com"
export LLMDBENCH_OPENSHIFT_TOKEN="..."
export LLMDBENCH_OPENSHIFT_NAMESPACE="..."
export LLMDBENCH_HF_TOKEN="..."
export LLMDBENCH_QUAY_USER="..."
export LLMDBENCH_QUAY_PASSWORD="..."
```

## A complete list of available variables (and its default values) can be found by running
 `cat hack/setup/env.sh | grep "^export LLMDBENCH_"`

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
./deploy.sh --step 07_smoketest_standalone_models.sh
./deploy.sh -s 7
```