## `llm-d`-benchmark

This repository provides an automated workflow for benchmarking LLM inference using the `llm-d` stack. It includes tools for deployment, experiment execution, data collection, and teardown across multiple environments and deployment styles.

### Goal

Provide a single source of automation for repeatable and reproducible experiments and performance evaluation on `llm-d`.

### 📦 Repository Setup

```
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
./setup/install_deps.sh
```

## Quickstart

#### Standup an `llm-d` stack model (default deployment method is `llm-d-modelservice`, serving `llama-1b`), run a harness (default `vllm-benchmark`) with a load profile (default `simple-random`) and teardown the stack

```
./e2e.sh
```

####  Run harness `inference-perf` with load profile `chatbot_synthetic` againsta a pre-deployed stack

```
./run.sh --harness inference-perf --workload chatbot_synthetic --method <a string that matches a inference service or pod>`
```

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

Load Generator (python code) which drives the benchmark load. Today, llm-d-benchmark supports [fmperf](https://github.com/fmperf-project/fmperf), [inference-perf](https://github.com/kubernetes-sigs/inference-perf), [guidellm](https://github.com/vllm-project/guidellm.git) and the benchmarks found on the `benchmarks` folder on [vllm](https://github.com/vllm-project/vllm.git). There are ongoing efforts to consolidate and provide an easier way to support different load generators.

#### Workload

Workload is the actual benchmark load specification which includes the LLM use case to benchmark, traffic pattern, input / output distribution and dataset. Supported workload profiles can be found under `workload/profiles`.

> [!IMPORTANT]
> The triple `<scenario>`,`<harness>`,`<workload>`, combined with the standup/teardown capabilities provided by [llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra.git) and [llm-d-modelservice](https://github.com/llm-d/llm-d-model-service.git) should provide enough information to allow an experiment to be reproduced.

### Dependecies

- [llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra.git)
- [llm-d-modelservice](https://github.com/llm-d/llm-d-model-service.git)
- [fmperf](https://github.com/fmperf-project/fmperf)
- [inference-perf](https://github.com/kubernetes-sigs/inference-perf)
- [guidellm](https://github.com/vllm-project/guidellm.git)
- [vllm](https://github.com/vllm-project/vllm.git)

## Topics

#### [Lifecycle](docs/lifecycle.md)
#### [Reproducibility](docs/lifecycle.md)
#### [Observability](docs/observability.md)
#### [Quickstart](docs/quickstart.md)
#### [FAQ](docs/faq.md)

## Contribute

- [Instructions on how to contribute](CONTRIBUTING.md) including details on our development process and governance.
- We use Slack to discuss development across organizations. Please join: [Slack](https://inviter.co/llm-d-slack). There is a `sig-benchmarking` channel there.
- We host a weekly standup for contributors on Thursdays at 13:30 ET. Please join: [Meeting Details](https://calendar.google.com/calendar/u/0?cid=NzA4ZWNlZDY0NDBjYjBkYzA3NjdlZTNhZTk2NWQ2ZTc1Y2U5NTZlMzA5MzhmYTAyZmQ3ZmU1MDJjMDBhNTRiNEBncm91cC5jYWxlbmRhci5nb29nbGUuY29t). The meeting notes can be found [here](https://docs.google.com/document/d/1njjeyBJF6o69FlyadVbuXHxQRBGDLcIuT7JHJU3T_og/edit?usp=sharing). Joining the [llm-d google groups](https://groups.google.com/g/llm-d-contributors) will grant you access.

## License

This project is licensed under Apache License 2.0. See the [LICENSE file](LICENSE) for details.
