# KubeCon NA 2025 Tutorial

[A Cross-Industry Benchmarking Tutorial for Distributed LLM Inference on Kubernetes](https://kccncna2025.sched.com/event/27FXL/tutorial-a-cross-industry-benchmarking-tutorial-for-distributed-llm-inference-on-kubernetes-jing-chen-ibm-research-junchen-jiang-university-of-chicago-ganesh-kudleppanavar-nvidia-samuel-monson-red-hat-jason-kramberger-google?iframe=yes&w=100%&sidebar=yes&bg=no)

This is a step-by-step tutorial accompanying our KubeCon North America 2025 talk on benchmarking distributed LLM inference, taking place on November 11, 2025. In this tutorial, we will walk through example configurations and benchmarking setups used in our talk. These examples are designed to help you replicate and understand the performance evaluation of [`llm-d`](https://github.com/llm-d/llm-d), a distributed LLM inference systems.

We provide detailed examples for the following components:

- Workload Configuration
- Scenario Files
- Experiment Files
- Expected Output
- Analysis Results

We showcase how to use the following open-source benchmarking tools (a.k.a. "harness" in `llm-d-benchmark` terms) to run configuration sweeps and identify optimal setups for `llm-d` using `llm-d-benchmark`:

- [Inference-Perf](https://github.com/kubernetes-sigs/inference-perf)
- [GuideLLM](https://github.com/vllm-project/guidellm)
- [vllm-benchmark](https://github.com/vllm-project/vllm/tree/main/benchmarks)

Our goal is to demonstrate how to systematically benchmark and tune distributed LLM inference workloads using real-world tools and configurations.

Follow along with this tutorial during our talk to get hands-on experience!


## Prerequisites

1. Start a `kind` cluster

```
kind create cluster
```

1. Install dependencies

```
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
./setup/install_deps.sh
```


## PD Disaggregation

- Scenario: kubecon2025/pd-disaggregation-scenario.sh
- Experiment: [pd-disaggreation.yaml](../experiments/pd-disaggregation.yaml)

Command:

```
./setup/e2e.sh -c kubecon2025/pd-disaggregation-scenario.sh -e pd-disaggregation.yaml
```