## Concept
`llm-d-benchmark` provides its own automated framework for the standup of stacks serving large language models in a Kubernetes cluster.

## Motivation
In order to allow reproducible and flexible experiments, and taking into account that the configuration paramaters have significant impact on the overall performance, it is necessary to provide the user with the ability to `standup` and `teardown` stacks.

## Methods
Currently, two main standup methods are supported
a) "Standalone", with multiple VLLM `pods` controlled by a `deployment` behind a single `service`
b) "llm-d", which leverages a combination of [llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra.git) and [llm-d-modelservice](https://github.com/llm-d/llm-d-model-service.git) to deploy a full-fledged `llm-d` stack

## Scenarios
All the information required for the standup of a stack is contained on a "scenario file". This information is encoded in the form of environment variables, with default values defined in `config/defaults.yaml` which can be then overriden inside a [scenario file](../config/scenarios) (YAML-based) or via [specification templates](../config/specification) (Jinja2 `.yaml.j2` files).

### Multi-Stack Scenarios

A scenario may define more than one stack in its `scenario:` list. Standup
iterates every per-stack step across all stacks (in parallel, bounded by
`--parallel`), so you can stand up N models behind one gateway in a single
`llmdbenchmark standup` invocation. Scenario-wide config (gateway class,
WVA controller, shared HTTPRoute, chart versions) lives in an optional
top-level `shared:` block that's merged into every stack before per-stack
overrides.

Cluster-scoped infrastructure that would race with itself across N parallel
standup executions is deduplicated at render time - only the first stack
emits the istio control-plane helmfile and the `infra-llmdbench` Helm
release; subsequent stacks render empty files for those templates. WVA
controller installation is deduplicated at the step level (one per
`wva.namespace`).

Currently shipped multi-stack guide:

- [`guides/multi-model-wva`](../config/scenarios/guides/multi-model-wva.yaml) -
  two models (Qwen3-0.6B + Meta-Llama-3.1-8B), each with its own EPP +
  InferencePool + VariantAutoscaling + HPA, one shared WVA controller,
  one HTTPRoute with two backendRefs routing by path prefix
  (`/qwen3-06b/*` -> Qwen pool, `/llama-31-8b/*` -> Llama pool).

See [`config/README.md`](../config/README.md#method-1-scenario-file-recommended-for-deployment-specific-config)
for the `shared:` merge semantics and the developer guide's
[Multi-Stack Scenarios](developer-guide.md#multi-stack-scenarios-and-the-shared-block)
section for the render-engine details.

`--stack NAME[,NAME...]` (also `LLMDBENCH_STACK=NAME`) restricts standup to
a subset of rendered stacks - handy for re-deploying a single pool after a
scenario edit without tearing down siblings. Global steps (cluster admin
prereqs, shared-infra helmfile, WVA controller install, scenario-wide
PVCs) still run as usual; only per-stack steps (06+ for standup) are
filtered. Unknown names fail loudly with a list of valid ones.

```bash
# One stack:
llmdbenchmark --spec guides/multi-model-wva standup -p my-namespace --stack qwen3-06b

# Multiple named stacks (comma-separated):
llmdbenchmark --spec guides/multi-model-wva standup -p my-namespace --stack qwen3-06b,llama-31-8b
```

The same flag works on `smoketest`, `run`, and `teardown` with identical
semantics, so you can scope every lifecycle phase to the same subset.

## Multiple steps
The full standup of a stack is a multi-step process. The [lifecycle](lifecycle.md) document go into more details explaning the meaning of each different individual step.

## Use
A scenario file has to be manually crafted as a YAML file. Once crafted, it can be used by `llmdbenchmark standup`, `llmdbenchmark run` or `llmdbenchmark teardown` commands. Its access is controlled by the following parameters.

> [!NOTE]
> `llmdbenchmark experiment` is a command that **combines** `llmdbenchmark standup`, `llmdbenchmark run` and `llmdbenchmark teardown` into a single operation. Therefore, the command line parameters supported by the former is a combination of the latter three.

The scenario parameters can be roughly categorized in four groups:
- Target-specific (Cluster API access, authentication tokens, standup methods and models)

| Variable                                     | Meaning                                        | Note                                                  |
| -------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------- |
| LLMDBENCH_CLUSTER_URL                        | URL to API access to Kubernetes cluster        | "auto" means "current" (e.g. `~/.kube/config`) is used|
| LLMDBENCH_CLUSTER_TOKEN                      | Used to authenticate to the cluster            | Ignored for LLMDBENCH_CLUSTER_URL="auto"              |
| LLMDBENCH_HF_TOKEN                           | Hugging face token                             | Required for gated models; optional for public models (auto-detected) |
| LLMDBENCH_DEPLOY_SCENARIO                    | File containing multiple environment variables which will override defaults | If not specified, defaults to (empty) `none.yaml`. Can be overriden with CLI parameter `-c/--scenario` |
| LLMDBENCH_DEPLOY_MODEL_LIST                  | List (comma-separated values) of models to be run against | Default=`meta-llama/Llama-3.2-1B-Instruct`. Can be overriden with CLI parameter `-m/--models` |
| LLMDBENCH_DEPLOY_METHODS                       | List (comma-separated values) of standup methods | Default=`modelservice`. Can be overriden with CLI parameter `-t/--methods` |

> [!TIP]
> In case the full path is ommited for the scenario file (either by setting `LLMDBENCH_DEPLOY_SCENARIO` or CLI parameter `-c/--scenario`, it is assumed that the scenario exists inside the `config/scenarios` folder

- "Common" VLLM parameters, applicable to any standup method

| Variable                                     | Meaning                                                                 | Note                                                                                                                                     |
|----------------------------------------------|-------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| LLMDBENCH_VLLM_COMMON_NAMESPACE              | Namespace where stack gets stood up                                     | Default=`llmdbench`. Can be overriden with CLI parameter `-p/--namespace`                                                                |
| LLMDBENCH_IGNORE_FAILED_VALIDATION           | Ignore failed sanity checks and continue to deployment                  | Default=`True`. Capacity Planner will perform a sanity check on vLLM parameters such as valid TP, max-model-len, KV cache availability.  |
| LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY     | GPU memory for `LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE` (e.g. `80`) | Default=`auto`, will try to guess GPU memory from `LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE`                                           |
| LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT        | Service Account for stack                                               |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE   | Accelerator type (e.g., `nvidia.com/gpu`)                               | "auto" means, will query the cluster to discover                                                                                         |
| LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE       | Network type (e.g., `rdma/roce_gdr`)                                    |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_VLLM_ALLOW_LONG_MAX_MODEL_LEN |                                            |                                                |
| LLMDBENCH_VLLM_COMMON_VLLM_SERVER_DEV_MODE          |                                            |  e.g., `0, 1` |
| LLMDBENCH_VLLM_COMMON_VLLM_LOAD_FORMAT              |                                            |  e.g., `safetensors, tensorizer, runai_streamer, fastsafetensors` |
| LLMDBENCH_VLLM_COMMON_VLLM_LOGGING_LEVEL            |                                            |  e.g., `DEBUG, INFO, WARNING`                                              |
| LLMDBENCH_VLLM_COMMON_ENABLE_SLEEP_MODE             |                                            |  e.g., `true, false` |
| LLMDBENCH_VLLM_COMMON_NETWORK_NR             |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_AFFINITY               |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_REPLICAS               |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM     |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM       |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR         |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL   |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_CPU_NR                 |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_CPU_MEM                |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN          |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_BLOCK_SIZE             |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_MAX_NUM_BATCHED_TOKENS |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_PVC_NAME               |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS      |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE   |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT   |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_HF_TOKEN_KEY           |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME          |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_INFERENCE_PORT         |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_FQDN                   |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_TIMEOUT                |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_ANNOTATIONS            |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML        |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_INITIAL_DELAY_PROBE    |                                                                         |                                                                                                                                          |
| LLMDBENCH_VLLM_COMMON_POD_SCHEDULER          |                                                                         |                                                                                                                                          |

- "Standalone"-specific VLLM parameters

| Variable                                                | Meaning                                    | Note                                           |
| ------------------------------------------------------- | ------------------------------------------ | ---------------------------------------------- |
| LLMDBENCH_VLLM_COMMON_MODEL_LOADER_EXTRA_CONFIG     |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT                |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_PREPROCESS                    |                                            | e.g., `source /setup/preprocess/standalone-preprocess.sh ; /setup/preprocess/standalone-preprocess.py`                                              |
| LLMDBENCH_VLLM_STANDALONE_ROUTE                         |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_HTTPROUTE                     |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_ARGS                          |                                            |                                                |
| LLMDBENCH_VLLM_STANDALONE_EPHEMERAL_STORAGE             |                                            |                                                |

- Gateway provider

| Variable                                     | Meaning                                                                | Note                                                                                                     |
| -------------------------------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME | Gateway implementation used for the inference gateway                 | Default=`istio`. Set to `agentgateway` to use the agentgateway data plane instead of Istio               |

### Switching from Istio to agentgateway

By default, `llm-d-benchmark` deploys [Istio](https://istio.io/) as the gateway provider for the `modelservice` deployment method.  To use [agentgateway](https://agentgateway.dev/) instead, add a `gateway` block to your scenario YAML:

```yaml
scenario:
  - name: "my-stack"
    gateway:
      className: agentgateway       # default is "istio"

    modelservice:
      enabled: true
    # ... rest of scenario config
```

That single change is all that's needed.  The benchmark tool handles everything else automatically:

1. **Installs agentgateway** -- the controller and CRDs are installed via helmfile during step 02 (admin prerequisites), the same way Istio is installed
2. **Configures the Gateway resource** -- the llm-d-infra Helm chart creates a `Gateway` with `gatewayClassName: agentgateway`
3. **OpenShift SCC** -- on OpenShift clusters, a minimal custom SCC (`llmdbench-agentgateway`) is automatically created and granted to the gateway service account, allowing the proxy to run as UID 10101 with `NET_BIND_SERVICE`

#### Differences from Istio

| Aspect                    | Istio                                                  | agentgateway                                                          |
|---------------------------|--------------------------------------------------------|-----------------------------------------------------------------------|
| Gateway pod creation      | Created by the llm-d-infra Helm chart directly         | Created dynamically by the agentgateway controller                    |
| `gatewayParameters`       | Uses `ConfigMap`-based `parametersRef`                 | Not used -- agentgateway manages its own `AgentgatewayParameters` CRD |
| OpenShift compatibility   | Built-in via `floatingUserId` (uses namespace UID range) | Requires custom SCC (auto-created by the tool)                      |
| Service name              | `infra-{release}-inference-gateway-istio`              | `infra-{release}-inference-gateway`                                   |

#### Example scenarios using agentgateway

- [`config/scenarios/examples/cpu.yaml`](../config/scenarios/examples/cpu.yaml) -- CPU-only deployment
- [`config/scenarios/guides/inference-scheduling.yaml`](../config/scenarios/guides/inference-scheduling.yaml) -- inference scheduling guide

- "llm-d"-specific VLLM paramaters

| Variable                                          | Meaning                                         | Note                                            |
| ------------------------------------------------- | ----------------------------------------------- | ----------------------------------------------- |
| LLMDBENCH_VLLM_INFRA_CHART_NAME                   |                                                 |                                                 |
| LLMDBENCH_VLLM_INFRA_CHART_VERSION                |                                                 |                                                 |
| LLMDBENCH_VLLM_INFRA_GATEWAY_CPU_REQUEST          | Gateway CPU request                             | Default=`4`                                     |
| LLMDBENCH_VLLM_INFRA_GATEWAY_CPU_LIMIT            | Gateway CPU limit                               | Default=`16`                                    |
| LLMDBENCH_VLLM_INFRA_GATEWAY_MEMORY_REQUEST       | Gateway memory request                          | Default=`4Gi`                                   |
| LLMDBENCH_VLLM_INFRA_GATEWAY_MEMORY_LIMIT         | Gateway memory limit                            | Default=`16Gi`                                  |
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
| LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE |                                                 |                                                 |
| LLMDBENCH_VLLM_MODELSERVICE_GAIE_MONITORING_PROMETHEUS_ENABLED | Enable Prometheus ServiceMonitor for GAIE EPP component metrics                                                 | `true` (default) or `false` false                                            |
