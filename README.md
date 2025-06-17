## `llm-d`-benchmark

This repository provides an automated workflow for benchmarking LLM inference using the `llm-d` stack. It includes tools for deployment, experiment execution, data collection, and teardown across multiple environments and deployment styles.

### Goal

To provide a single source of automation for repeatable and reproducible experiments and performance evaluation on `llm-d`.

### Architecture

The benchmarking system drives synthetic or trace-based traffic into an llm-d-powered inference stack, orchestrated via Kubernetes. Requests are routed through a scalable load generator, with results collected and visualized for latency, throughput, and cache effectiveness.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Logo" src="./docs/images/llm-d-benchmarking.jpg" width=100%>
  </picture>
</p>

### Goals

#### Reproducibility

Each benchmark run collects enough information to enable the execution on different clusters/environments with minimal setup effort

#### Flexibility

Multiple load generators and multiple load profiles available, in a plugable architecture that allows expansion

#### Well defined set of Metrics

Define and measure a representative set of metrics that allows not only meaningful comparisons between different stacks, but also performance characterization for different components.

For a discussion of candidate relevant metrics, please consult this [document](https://docs.google.com/document/d/1SpSp1E6moa4HSrJnS4x3NpLuj88sMXr2tbofKlzTZpk/edit?resourcekey=0-ob5dR-AJxLQ5SvPlA4rdsg&tab=t.0#heading=h.qmzyorj64um1)

| Category | Metric | Unit |
| ---------| ------- | ----- |
| Throughput | Output tokens / second | tokens / second |
| Throughput | Input tokens / second | tokens / second |
| Throughput | Requests / second | qps |
| Latency    | Time per output token (TPOT) | ms per output token |
| Latency    | Time to first token (TTFT) | ms |
| Latency    | Time per request (TTFT + TPOT * output length) | seconds per request |
| Latency    | Normalized time per output token (TTFT/output length +TPOT) aka NTPOT | ms per output token |
| Latency    | Inter Token Latency (ITL) - Time between decode tokens within a request | ms per output token |
| Correctness | Failure rate | queries |
| Experiment | Benchmark duration | seconds |

### Relevant collection of Workloads

Define a mix of workloads that express real-world use cases, allowing for `llm-d` performance characterization, evaluation, stress investigation.

For a discussion of relevant workloads, please consult this [document](https://docs.google.com/document/d/1Ia0oRGnkPS8anB4g-_XPGnxfmOTOeqjJNb32Hlo_Tp0/edit?tab=t.0)

| Workload                               | Use Case            | ISL    | ISV   | OSL    | OSV    | OSP    | Latency   |
| -------------------------------------- | ------------------- | ------ | ----- | ------ | ------ | ------ | ----------|
| Interactive Chat                       | Chat agent          | Medium | High  | Medium | Medium | Medium | Per token |
| Classification of text                 | Sentiment analysis  | Medium |       | Short  | Low    | High   | Request   |
| Classification of images               | Nudity filter       | Long   | Low   | Short  | Low    | High   | Request   |
| Summarization / Information Retrieval  | Q&A from docs, RAG  | Long   | High  | Short  | Medium | Medium | Per token |
| Text generation                        |                     | Short  | High  | Long   | Medium | Low    | Per token |
| Translation                            |                     | Medium | High  | Medium | Medium | High   | Per token |
| Code completion                        | Type ahead          | Long   | High  | Short  | Medium | Medium | Request |
| Code generation                        | Adding a feature    | Long   | High  | Medium | High   | Medium | Request |

### Design and Roadmap

`llm-d-benchmark` follows the practice of its parent project (`llm-d`) by having also it is own [Northstar design](https://docs.google.com/document/d/1DtSEMRu3ann5M43TVB3vENPRoRkqBr_UiuwFnzit8mw/edit?tab=t.0#heading=h.9a3894cbydjw) (a work in progress)

### Main concepts (identified by specific directories)

#### Scenarios

Pieces of information identifying a particular cluster. This information includes, but it is not limited to, GPU model, llm model and llm-d parameters (an environment file, and optionally a `values.yaml` file for llm-d-deployer)

#### Harness

Load Generator (python code), written using software facilites available at <https://github.com/fmperf-project/fmperf>.

> [!NOTE]
> This will be expanded with additional load generators in the future (e.g. [inference-perf](https://github.com/kubernetes-sigs/inference-perf) )

#### Workload

FMPerf workload specification, with load profile (e.g., `share-gpt` vs `long-input`) and load levels (e.g., QPS values). IMPORTANT: these definitions will be expanded with specifications for other load generators.

> [!IMPORTANT]
> The triple `<scenario>`,`<harness>`,`<workload>`, combined with the standup/teardown capabilities provided by llm-d-deployer (<https://github.com/llm-d/llm-d-deployer>) should provide enough information to allow an experiment to be reproduced.

### Dependecies

- llm-d-deployer (<https://github.com/llm-d/llm-d-deployer>)
- fm-perf: <https://github.com/fmperf-project/fmperf>

### 📦 Repository Setup

```
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
```

## Quickstart

#### Standing up llm-d for experimentation and benchmarking

```
export LLMDBENCH_CLUSTER_HOST="https://api.fmaas-platform-eval.fmaas.res.ibm.com"
export LLMDBENCH_CLUSTER_TOKEN="..."
```

> [!TIP]
> You can simply use your current context. **After running kubectl/oc login**, just set `export LLMDBENCH_CLUSTER_HOST=auto` (and leave LLMDBENCH_CLUSTER_TOKEN unconfigured)

> [!IMPORTANT]
> No matter which method used (i.e., fully specify `LLMDBENCH_CLUSTER_HOST` and `LLMDBENCH_CLUSTER_TOKEN` or simply use the current context), there is an additional variable which will always require definition: `LLMDBENCH_HF_TOKEN`

> [!CAUTION]
> Please make sure the environment variable `LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS` points to a storage class specific to your cluster. The default value will most likely fail.

A complete list of available variables (and its default values) can be found by running
 `cat setup/env.sh | grep "^export LLMDBENCH_" | sort`

> [!NOTE]
> The `namespaces` specified by the environment variables `LLMDBENCH_VLLM_COMMON_NAMESPACE` and `LLMDBENCH_FMPERF_SERVICE_ACCOUNT` will be automatically created.

> [!TIP]
> If you want all generated `yaml` files and all data collected to reside on the same directory, set the environment variable `LLMDBENCH_CONTROL_WORK_DIR` explicitly before starting execution.

#### List of "standup steps"

Run the command line with the option `-h` in order to produce a list of steps

```
./setup/standup.sh -h
```

> [!NOTE]
> Each individual "step file" is named in a way that briefly describes each one the multiple steps required for a full deployment.

> [!TIP]
> Steps 0-5 can be considered "preparation" and can be skipped in most deployments.

#### to dry-run

```
./setup/standup.sh -n
```

### Deployment

vLLM instances can be deployed by one of the following methods:

- "standalone" (a simple deployment with services associated to the deployment)
- "deployer" (invoking \"llm-d-deployer\").

This is controlled by the environment variable LLMDBENCH_DEPLOY_METHODS (default "deployer"). The value of the environment variable can be overriden by the paraemeter `-t/--types` (applicable for both `teardown.sh` and `standup.sh`)

> [!WARNING]
> At this time, only **one simultaneous** deployment method is supported

All available models are listed and controlled by the variable `LLMDBENCH_DEPLOY_MODEL_LIST`. The value of the above mentioned environment variable can be overriden by the paraemeter `-m/--model` (applicable for both `teardown.sh` and `standup.sh`).

> [!WARNING]
> At this time, only **one simultaneous** model is supported

> [!TIP]
> The following aliases can be used in place of the full model name, for convenience (_llama-3b_ -> `meta-llama/Llama-3.2-3B-Instruct`, _llama-8b_ -> `meta-llama/Llama-3.1-8B-Instruct`, _llama-70b_ -> `meta-llama/Llama-3.1-70B-Instruct`, _llama-17b_ -> `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`)

### Scenarios

All relevant variables to a particular experiment are stored in a "scenario" (folder aptly named).

The expectation is that an experiment is run by initially executing:

```
source scenario/<scenario name>
```

### Lifecycle (Standup/Run/Teardown)

At this point, with all the environment variables set (tip, `env | grep ^LLMDBENCH_ | sort`) you should be ready to deploy and test

```
./setup/standup.sh
```

> [!NOTE]
> The scenario can also be indicated as part of the command line optios for `standup.sh` (e.g. `./setup/standup.sh -c ocp_H100MIG_deployer_llama-3b`)

To re-execute only individual steps (full name or number):

```
./setup/standup.sh --step 08_smoketest.sh
./setup/standup.sh -s 7
./setup/standup.sh -s 3-5
./setup/standup.sh -s 5,7
```

Once llm-d is fully deployed, an experiment can be run

```
./run.sh
```

> [!IMPORTANT]
> This command will run an experiment, collect data and perform an initial analysis (generating statistics and plots). One can go straight to the analysis by adding the option `-z`/`--skip` to the above command

> [!NOTE]
> The scenario can also be indicated as part of the command line optios for `run.sh` (e.g., `./run.sh -c ocp_L40_standalone_llama-8b`)

Finally, cleanup everything

```
./setup/teardown.sh
```

> [!NOTE]
> The scenario can also be indicated as part of the command line optios for `teardown.sh` (e.g., `./teardown.sh -c kubernetes_H200_deployer_llama-8b`)

## Reproducibility

All the information collected inside the directory pointed by the environment variable `LLMDBENCH_CONTROL_WORK_DIR` should be enough to allow others to reproduce the experiment with the same parameters. In particular, all the parameters - always exposed as environment variables - applied to `llm-d` or `vllm` stacks can be found at `${LLMDBENCH_CONTROL_WORK_DIR}/environment/variables`

A sample output of the contentx of `${LLMDBENCH_CONTROL_WORK_DIR}` for a very simple experiment is shown here

```
./analysis
./analysis/data
./analysis/data/stats.txt
./analysis/plots
./analysis/plots/latency_analysis.png
./analysis/plots/README.md
./analysis/plots/throughput_analysis.png
./setup
./setup/yamls
./setup/yamls/05_pvc_workload-pvc.yaml
./setup/yamls/pod_benchmark-launcher.yaml
./setup/yamls/05_b_service_access_to_fmperf_data.yaml
./setup/yamls/07_deployer_values.yaml
./setup/yamls/05_namespace_sa_rbac_secret.yaml
./setup/yamls/04_prepare_namespace_llama-3b.yaml
./setup/yamls/05_a_pod_access_to_fmperf_data.yaml
./setup/yamls/03_cluster-monitoring-config_configmap.yaml
./setup/commands
./setup/commands/1748350741979704000_command.log
...
./setup/commands/1748350166902915000_command.log
./setup/sed-commands
./results
./results/LMBench_short_input_qps0.5.csv
./results/pod_log_response.txt
./environment
./environment/context.ctx
./environment/variables
./workload
./workload/harnesses
./workload/profiles
./workload/profiles/sanity_short-input.yaml
```

## Observability

As of today, observability, via Grafana dashboards, is considered to be outside of the scope for `llm-d-benchmark`. Please refer to the [installation guide on llm-d-deployer](https://github.com/llm-d/llm-d-deployer/tree/main/quickstart#grafana-dashboards) for instructions on how to enable it.

### Examples

These plots, automatically generated, were used to showcase the difference between a baseline `vLLM` deployment and `llm-d` (for models Llama 4 Scout and Lllama 3.1 70B)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="vllm vs llm-d comparison" src="./docs/images/scenarios_1_2_3_comparison.png" width=100%>
  </picture>
</p>

## Quickstart K8s Benchmark Launcher for Existing Stacks

For a simplified workflow that includes analysis of benchmark results, check out the `quickstart-existing-stack-benchmark` launcher. This workflow provides:

- Easy deployment and execution of benchmarks on Kubernetes
- Support for comparing multiple LLM models
- Generation of comprehensive performance visualizations

### Quickstart Workflows

1. **Single Model Benchmark**: Run benchmarks for a single model with automated analysis
   - See [Single Model Quickstart](quickstart-existing-stack-benchmark/README.md) for details

2. **Multi-Model Comparison**: Compare performance across multiple LLM models
   - See [Multi-Model Comparison Quickstart](quickstart-existing-stack-benchmark/Compare-README.md) for details

To get started, navigate to the `quickstart-existing-stack-benchmark` directory and follow the instructions in the respective README files.

## Contribute

- [Instructions on how to contribute](CONTRIBUTING.md) including details on our development process and governance.
- We use Slack to discuss development across organizations. Please join: [Slack](https://inviter.co/llm-d-slack). There is a `sig-benchmarking` channel there.
- We host a weekly standup for contributors on Thursdays at 13:30 ET. Please join: [Meeting Details](https://calendar.google.com/calendar/u/0?cid=NzA4ZWNlZDY0NDBjYjBkYzA3NjdlZTNhZTk2NWQ2ZTc1Y2U5NTZlMzA5MzhmYTAyZmQ3ZmU1MDJjMDBhNTRiNEBncm91cC5jYWxlbmRhci5nb29nbGUuY29t). The meeting notes can be found [here](https://docs.google.com/document/d/1njjeyBJF6o69FlyadVbuXHxQRBGDLcIuT7JHJU3T_og/edit?usp=sharing). Joining the [llm-d google groups](https://groups.google.com/g/llm-d-contributors) will grant you access.

## License

This project is licensed under Apache License 2.0. See the [LICENSE file](LICENSE) for details.
