# Deploy and Teardown benchmark tests

## Clone llm-d-benchmark repo
```
git clone https://github.com/neuralmagic/llm-d-benchmark
cd llm-d-benchmark/hack/setup
```

## Minimal set of required environment variables
```
export LLMDBENCH_OPENSHIFT_HOST="https://api.fmaas-platform-eval.fmaas.res.ibm.com"
export LLMDBENCH_OPENSHIFT_TOKEN="..."
export LLMDBENCH_OPENSHIFT_NAMESPACE="..."
export LLMDBENCH_HF_TOKEN="..."
export LLMDBENCH_QUAY_USER="..."
export LLMDBENCH_QUAY_PASSWORD="..."
```
## IMPORTANT: in case you want to simply use the current context, just set `export LLMDBENCH_OPENSHIFT_HOST=auto`

## A complete list of available variables (and its default values) can be found by running
 `cat env.sh | grep ^export LLMDBENCH_ | sort`

## list of steps
```
./deploy.sh -h
```

## to dry-run
```
./deploy.sh -n
```

## two types of VLLM deployments are available: "standalone" (a simple deployment with services) and "p2p" (using a helm chart and accessed via inference gateway). This is controlled by the variable LLMDBENCH_DEPLOY_ENVIRONMENT_TYPES (default "standalone,p2p")


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
./deploy.sh -s 3-5
./deploy.sh -s 5,7
```