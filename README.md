# Benchmark deploy, execution, data collection, analysis and teardown

## Clone llm-d-benchmark repo
```
git clone https://github.com/neuralmagic/llm-d-benchmark
cd llm-d-benchmark/hack/deploy
```

## Minimal set of required environment variables
```
export LLMDBENCH_CLUSTER_HOST="https://api.fmaas-platform-eval.fmaas.res.ibm.com"
export LLMDBENCH_CLUSTER_TOKEN="..."
export LLMDBENCH_CLUSTER_NAMESPACE="..."
```
### IMPORTANT: in case you want to simply use the current context, just set `export LLMDBENCH_CLUSTER_HOST=auto`

## In case you need to create a pull secret and hugging face token(s) these additional variables will be needed
```
export LLMDBENCH_HF_TOKEN="..."
export LLMDBENCH_QUAY_USER="..."
export LLMDBENCH_QUAY_PASSWORD="..."
```
### IMPORTANT: if step 3 (`03_prepare_namespace.sh`) was already executed, then these variables are no longer needed.
### IMPORTANT: these tokens/pull secrets survive multiple execution of `cleanup.sh`

## A complete list of available variables (and its default values) can be found by running
 `cat env.sh | grep ^export LLMDBENCH_ | sort`

## list of steps
```
./up.sh -h
```

## to dry-run
```
./up.sh -n
```

## VLLMs can be deployed by one of the following methods:
* #### "standalone" (a simple deployment with services associated to the deployment)
* #### "p2p" (using a helm chart and accessed via inference gateway).
#### This is controlled by the environment variable LLMDBENCH_DEPLOY_METHODS (default "standalone,p2p")
#### The value of the environment variable can be overriden by the paraemeter `-t/--types` (applicable for both `cleanup.sh` and `deploy.sh`)

## All available models are listed and controlled by the variable `LLMDBENCH_DEPLOY_MODEL_LIST`
#### The value of the environament variable can be overriden by the paraemeter `-m/--model` (applicable for both `cleanup.sh` and `deploy.sh`)

## Scenarios
#### All relevant variables to a particular experiment are stored in a "scenario" (folder aptly named).
#### The expectation is that an experiment is run by initially executing :

```
source scenario/<scenario name>
```

## At this point, with all the environment variables set (tip, `env | grep ^LLMDBENCH_ | sort`) you should be ready to deploy and test
```
./up.sh
```

## IMPORTANT: the scenario can be indicated as part of the command line optios for `up.sh`

### to re-execute only individual steps (full name or number)
```
./up.sh --step 07_smoketest_standalone_models.sh
./up.sh -s 7
./up.sh -s 3-5
./up.sh -s 5,7
```

## Once everything is fully deployed, an experiment can be run
```
./run.sh
```
## IMPORTANT: the scenario can be indicated as part of the command line optios for `run.sh`

```
./run.sh -c ocp_H100MIG_p2p_llama-8b
```

## Finally, cleanup everything
```
./down.sh
```
