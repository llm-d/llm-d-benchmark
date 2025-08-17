## Concept
`llm-d-benchmark` provides its own automated framework for the standup of stacks serving large language models in a Kubernetes cluster.

## Motivation
In order to allow reproducible and flexible experiments, and taking into account that the configuration paramaters have significant impact on the overall performance, it is necessary to provide the user with the ability to `standup` and `teardown` stacks.

## Methods
Currently, two main standup methods are supported
a) "Standalone", with multiple VLLM `pods` controlled by a `deployment` behind a single `service`
b) "llm-d", which leverages a combination of [llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra.git) and [llm-d-modelservice](https://github.com/llm-d/llm-d-model-service.git) to deploy a full-fledged `llm-d` stack

## Scenarios
All the information required for the deployment of a stack is contained on a "scenario file". This information is encoded in the form of environment variables, with [default values](../setup/env.sh) which can be then overriden inside a [scenario file](../scenarios)

## Use
A scenario file has to be manually crafted as a text file with a list of `export LLMDBENCH_<VARIABLE NAME>` statements. Once crafted, it can used by `./setup/standup.sh`, `run.sh` or `setup/teardown.sh` executables. Its access is controlled by the following parameters.

> [!NOTE]
> `./e2e.sh` is an executable that **combines** `./setup/standup.sh`, `run.sh` and `setup/teardown.sh` into a singe operation. Therefore, the command line parameters supported by the former is a combination of the latter three.

The scenario parameters can be roughly categorized in four groups:
- Target-specific (Cluster API access, authentication tokens, standup methods and models)

| Variable                                     | Meaning                                        | Note                                                  |
| -------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------- |
| LLMDBENCH_CLUSTER_URL                        | URL to API access to Kubernetes cluster        | "auto" means "current" (e.g. `~/.kube/config`) is used|
| LLMDBENCH_CLUSTER_TOKEN                      | Used to authenticate to the cluster            | Ignored for LLMDBENCH_CLUSTER_URL="auto"              |
| LLMDBENCH_HF_TOKEN                           | Hugging face token                             | Required during standup for model downloading         |
| LLMDBENCH_DEPLOY_SCENARIO                    | Scenario file (containing a lists of env vars) | Can be overriden wit CLI parameter `-c/--scenario`    |
| LLMDBENCH_DEPLOY_MODEL_LIST                  | List (comma-seperated) of models to stand up   | Can be overriden wit CLI parameter `-m/--models`      |
| LLMDBENCH_DEPLOY_METHODS                     | List (comma-seperated) of standup methods      | Can be overriden wit CLI parameter `-t/--methods`     |

> [!TIP]
> In case the full path is ommited for the scenario file (either by setting `LLMDBENCH_DEPLOY_SCENARIO` or CLI parameter `-c/--scenario`, it is assumed that the scenario exists inside the `scenarios` folder

- "Common" VLLM parameters, applicable to any standup method

| Variable                                     | Meaning                                    | Note                                                      |
| -------------------------------------------- | ------------------------------------------ | --------------------------------------------------------- |
| LLMDBENCH_VLLM_COMMON_NAMESPACE              | Namespace where stack gets stood up        | Can be overriden wit CLI parameter `-p/--namespace`       |
| LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT        | Service Account for stack                  |                                                           |
| LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE   | Accelerator type (e.g., `nvidia.com/gpu`)  | "auto" means, will query the cluster to discover          |
| LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE       | Network type (e.g., `rdma/roce_gdr`)       |                                                           |
| LLMDBENCH_VLLM_COMMON_NETWORK_NR             |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_AFFINITY               |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_REPLICAS               |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR         |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL   |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_CPU_NR                 |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_CPU_MEM                |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN          |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_BLOCK_SIZE             |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_PVC_NAME               |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS      |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE   |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT   |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_HF_TOKEN_KEY           |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME          |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_INFERENCE_PORT         |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_FQDN                   |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_TIMEOUT                |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_ANNOTATIONS            |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML        |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_INITIAL_DELAY_PROBE    |                                            |                                                           |
| LLMDBENCH_VLLM_COMMON_POD_SCHEDULER          |                                            |                                                           |

- "Standalone"-specific VLLM parameters

| Variable                                                | Meaning                                    | Note                                           |
| ------------------------------------------------------- | ------------------------------------------ | ---------------------------------------------- |
| LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG     |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_VLLM_LOGGING_LEVEL            |                                            |  e.g., `DEBUG, INFO, WARNING`                                              |
| LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT                |                                            |  e.g., `source /setup/preprocess/standalone-preprocess.sh ; /setup/preprocess/standalone-preprocess.py`                                              |
| LLMDBENCH_VLLM_STANDALONE_PREPROCESS                    |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_ROUTE                         |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_HTTPROUTE                     |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_VLLM_ALLOW_LONG_MAX_MODEL_LEN |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_VLLM_SERVER_DEV_MODE          |                                            |  e.g., `safetensors, tensorizer, runai_streamer, fastsafetensors` |
| LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT              |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_ARGS                          |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_EPHEMERAL_STORAGE             |                                            |                                                |

- "llm-d"-specific VLLM paramaters

| Variable                                          | Meaning                                         | Note                                            |
| ------------------------------------------------- | ----------------------------------------------- | ----------------------------------------------- |
| LLMDBENCH_VLLM_INFRA_CHART_NAME                   |                                                 |                                                 |
| LLMDBENCH_VLLM_INFRA_CHART_VERSION                |                                                 |                                                 |
| LLMDBENCH_VLLM_GAIE_CHART_NAME                    |                                                 |                                                 |
| LLMDBENCH_VLLM_GAIE_CHART_VERSION                 |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_RELEASE               |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_VALUES_FILE           |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_ADDITIONAL_SETS       |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION         |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_CHART_NAME            |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY       |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY_URL   |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_URI_PROTOCOL          |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME    |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_ROUTE                 |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_EPP                   |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL       |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL        |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_GAIE_PRESETS          |                                                 |                                                 |
