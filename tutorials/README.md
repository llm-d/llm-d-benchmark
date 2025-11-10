# KubeCon NA 2025 Tutorial

A Cross-Industry Benchmarking Tutorial for Distributed LLM Inference on Kubernetes

<!-- [A Cross-Industry Benchmarking Tutorial for Distributed LLM Inference on Kubernetes](https://kccncna2025.sched.com/event/27FXL/tutorial-a-cross-industry-benchmarking-tutorial-for-distributed-llm-inference-on-kubernetes-jing-chen-ibm-research-junchen-jiang-university-of-chicago-ganesh-kudleppanavar-nvidia-samuel-monson-red-hat-jason-kramberger-google?iframe=no&w=100%25&sidebar=yes&bg=no) -->

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

1. Connect to a Kubernetes cluster

2. Install dependencies

```
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark

# Check out the stable latest commit
git reset --hard 2ac07a5a10da6d3fad5fd544a3770c38114e6f6d

# Set up virtual environment
python -m venv .venv
source .venv/bin/activate
./setup/install_deps.sh
```

## Simple Example with GuideLLM

- Scenario: `scenarios/basic.sh`
- Experiment: `experiments/smoke.sh`
- Workload: `workload/profiles/guidellm/concurrent_sweep.yaml.in`

### Run a basic scenario 

Examine the provided [basic scenario](./scenarios/basic.sh); fill in any fields marked TODO.
Make sure the correct cluster context is set then run the following commands:

```sh
export BASE_PATH="$(realpath ./tutorials)"

# Standup a simple llm-d deployment with single prefill and decode pods
./setup/standup.sh -c "${BASE_PATH}/scenarios/basic.sh"

# Run guidellm through `llm-d-benchmark`
./setup/run.sh -c "${BASE_PATH}/scenarios/basic.sh"

# Teardown the deployment
./setup/teardown.sh -c "${BASE_PATH}/scenarios/basic.sh"
```

> **Note** Running the `./setup/e2e.sh` script is equivalent to running the three scripts above in series.

You should now have a directory tree that includes the following:

```
/tmp/modelserve.none
├── analysis
│   └── guidellm_1762460407-none_llm-d-2b-instruct
│       └── summary.txt
├── environment
│   ├── context.ctx
│   └── variables
├── logs
│   ├── 06_deploy_vllm_standalone_models.log
│   ├── 07_deploy_setup.log
│   ├── 08_deploy_gaie.log
│   └── step.log
└── results
    └── guidellm_1762460407-none_llm-d-2b-instruct
        ├── benchmark_report,_results.json_0.yaml
        ├── benchmark_report,_results.json_1.yaml
        ├── benchmark_report,_results.json_2.yaml
        ├── benchmark_report,_results.json_3.yaml
        ├── benchmark_report,_results.json_4.yaml
        ├── benchmark_report,_results.json_5.yaml
        ├── benchmark_report,_results.json_6.yaml
        ├── results.json
        ├── stderr.log
        └── stdout.log
```

The most interesting files here are:

- `analysis/guidellm_1762460407-none_llm-d-2b-instruct/summary.txt`: A dump of GuideLLM's final console output
- `results/guidellm_1762460407-none_llm-d-2b-instruct/results.json`: The raw GuideLLM results
- `results/guidellm_1762460407-none_llm-d-2b-instruct/benchmark_report,_results.json_${step}.yaml`: GuideLLM results processed into llm-d-benchmark's common metrics format.

### Run a simple experiment

A llm-d-benchmark **experiment** takes a **scenario** and augments it by iterating configuration variables through a parameter sweep.

We will run a simple [smoke test scenario](./experiments/smoke.yaml) that iterates the deployment through every 2-GPU combination of prefill and decode pods. For each deployment it then iterates through three synthetic dataset configurations.

``` sh
export BASE_PATH="$(realpath ./tutorials)"

# Experiments must be run with the full e2e.sh script
./setup/e2e.sh -c "${BASE_PATH}/scenarios/basic.sh" -e "${BASE_PATH}/experiments/smoke.sh"
```

Experiment results will be exported to `<LLMDBENCH_CONTROL_WORK_DIR>.setup_<treatment_config>`. For example: `/tmp/modelserve.setup_1_1_1_1`.

## Precise Prefix Caching Aware Routing with Inference-Perf

WIP

## PD Disaggregation with vllm-benchmark

- Scenario: [pd-disaggregation/pd-disaggregation-scenario.sh](./pd-diaggregation/pd-disaggregation-scenario.sh)
- Experiment: [pd-disaggregation.yaml](https://raw.githubusercontent.com/llm-d/llm-d-benchmark/refs/heads/main/experiments/pd-disaggregation.yaml)

Command (from `llm-d-benchmark` root directory):

```sh
export BASE_PATH="$(realpath ./tutorials)"

./setup/e2e.sh -c "${BASE_PATH}/scenarios/pd-disaggregation.sh" -e pd-disaggregation.yaml
```
