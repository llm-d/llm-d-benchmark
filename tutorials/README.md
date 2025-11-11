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

- Scenario: [pd-disaggregation.sh](./scenarios/pd-disaggregation.sh)
- Experiment: [pd-disaggregation.yaml](https://raw.githubusercontent.com/llm-d/llm-d-benchmark/refs/heads/main/experiments/pd-disaggregation.yaml)

Command (from `llm-d-benchmark` root directory):

```sh
export BASE_PATH="$(realpath ./tutorials)"

./setup/e2e.sh -c "${BASE_PATH}/scenarios/pd-disaggregation.sh" -e pd-disaggregation.yaml
```

You should expect to see the following in your cluster.

```
==> Tue Nov 11 09:47:50 AM EST 2025 - /home/jing.chen2_ibm.com/Work/llm-d-benchmark/setup/standup.sh - === Running step: 00_ensure_llm-d-infra.py ===

==> Tue Nov 11 09:47:52 AM EST 2025 - /home/jing.chen2_ibm.com/Work/llm-d-benchmark/setup/standup.sh - === Running step: 01_ensure_local_conda.py ===
2025-11-11 09:47:53,717 - INFO - ⏭️  Environment variable "LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY" is set to 0, skipping local setup of conda environment

==> Tue Nov 11 09:47:53 AM EST 2025 - /home/jing.chen2_ibm.com/Work/llm-d-benchmark/setup/standup.sh - === Running step: 02_ensure_gateway_provider.py ===
2025-11-11 09:47:55,848 - INFO - 🔍 Ensuring gateway infrastructure (provider kgateway) is setup...
2025-11-11 09:47:55,848 - INFO - 🚀 Installing Kubernetes Gateway API (v1.3.0) CRDs...
2025-11-11 09:47:57,186 - INFO - ✅ Kubernetes Gateway API CRDs installed
2025-11-11 09:47:57,186 - INFO - 🚀 Installing Kubernetes Gateway API inference extension (v1.0.1) CRDs...
2025-11-11 09:47:58,146 - INFO - ✅ Kubernetes Gateway API inference extension CRDs installed
2025-11-11 09:47:58,146 - INFO - 🚀 Installing kgateway helm charts from oci://cr.kgateway.dev/kgateway-dev/charts (v2.0.3)
2025-11-11 09:48:00,879 - INFO - ✅ kgateway installed
2025-11-11 09:48:00,879 - INFO - ✅ Gateway control plane (provider kgateway) installed.
2025-11-11 09:48:04,231 - INFO - ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE automatically set to "nvidia.com/gpu"
2025-11-11 09:48:04,233 - INFO - Validating vLLM configuration against Capacity Planner... deployment will continue even if validation failed.
2025-11-11 09:48:04,233 - INFO - Deployment method is modelservice, checking for prefill and decode deployments
2025-11-11 09:48:04,233 - INFO - Validating prefill vLLM arguments for ['meta-llama/Llama-3.1-8B-Instruct'] ...
2025-11-11 09:48:04,233 - WARNING - ⚠️  Cannot determine accelerator memory. Please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY to enable Capacity Planner. Skipping GPU memory required checks, especially KV cache estimation.
2025-11-11 09:48:04,390 - INFO - 👉 Collecting model information....
2025-11-11 09:48:04,390 - INFO - ℹ️ meta-llama/Llama-3.1-8B-Instruct has a total of 8030261248 parameters
2025-11-11 09:48:04,390 - INFO - ℹ️ meta-llama/Llama-3.1-8B-Instruct requires 14.957527160644531 GB of memory
2025-11-11 09:48:04,390 - INFO - Validating decode vLLM arguments for ['meta-llama/Llama-3.1-8B-Instruct'] ...
2025-11-11 09:48:04,390 - WARNING - ⚠️  Cannot determine accelerator memory. Please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY to enable Capacity Planner. Skipping GPU memory required checks, especially KV cache estimation.
2025-11-11 09:48:04,471 - INFO - 👉 Collecting model information....
2025-11-11 09:48:04,471 - INFO - ℹ️ meta-llama/Llama-3.1-8B-Instruct has a total of 8030261248 parameters
2025-11-11 09:48:04,471 - INFO - ℹ️ meta-llama/Llama-3.1-8B-Instruct requires 14.957527160644531 GB of memory
2025-11-11 09:48:04,471 - INFO - 🔍 Checking for OpenShift user workload monitoring enablement...
/home/jing.chen2_ibm.com/Work/llm-d-benchmark/.venv/lib64/python3.11/site-packages/urllib3/connectionpool.py:1097: InsecureRequestWarning: Unverified HTTPS request is being made to host 'api.fusionv6.ete14.res.ibm.com'. Adding certificate verification is strongly advised. See: https://urllib3.readthedocs.io/en/latest/advanced-usage.html#tls-warnings
  warnings.warn(
2025-11-11 09:48:04,512 - INFO - OpenShift cluster detected
2025-11-11 09:48:04,780 - INFO - ✅ OpenShift user workload monitoring enabled
2025-11-11 09:48:08,228 - INFO - PVC 'model-pvc' already exists in namespace 'ai-workloads'.
2025-11-11 09:48:08,229 - INFO - Provisioning model storage…
2025-11-11 09:48:08,229 - INFO - ℹ️ Skipping pvc creation
2025-11-11 09:48:08,229 - INFO - 🔽 Launching download job for model: "meta-llama/Llama-3.1-8B-Instruct"
2025-11-11 09:48:08,229 - INFO - Launching model download job...
2025-11-11 09:48:08,418 - INFO - Generated YAML file at: /home/jing.chen2_ibm.com/data/pd-disaggregation/setup/yamls/04_ensure_model_namespace_prepared_download_pod_job.yaml
2025-11-11 09:48:08,418 - INFO - --> Deleting previous job 'download-model' (if it exists) to prevent conflicts...
2025-11-11 09:48:08,832 - INFO - Waiting for job download-model to complete...

```

You should see that prefill and decode pods are up and running:

```
2025-09-23 15:51:26 : 🔄 Processing model 1/1: meta-llama/Llama-3.1-8B-Instruct
2025-09-23 15:51:26 : 🚀 Installing helm chart "gaie-nam-release" via helmfile...
2025-09-23 15:51:28 : ✅ ai-workloads-meta-lla-1b4505f6-instruct-gaie helm chart deployed successfully
2025-09-23 15:51:28 : ℹ️ A snapshot of the relevant (model-specific) resources on namespace "ai-workloads":
2025-09-23 15:51:29 : ✅ Completed model deployment
```

Check out the pods. For example, if deployed in aggregate (aka "standalone"), you should expect to see the following, which demonstrates a running vLLM deployment (`vllm-standalone-llama-3-70b-6bd4bcdffd-k9t9w`) and a workload generator pod (`llmdbench-vllm-benchmark-launcher`) sending traffic to it.

```
$ kubectl get pods
NAME                                           READY   STATUS             RESTARTS   AGE
access-to-harness-data-workload-pvc            1/1     Running            0          11h
download-model-ps6sz                           0/1     Completed          0          1h
llmdbench-vllm-benchmark-launcher              1/1     Running            0          2m
vllm-standalone-llama-3-70b-6bd4bcdffd-k9t9w   1/1     Running            0          1h
```

The experiment will take some time to run to completion. You may decrease the experiment and harness run treatments list for simplicity. After the experiment finishes running, you should see the following results in `~/data/pd-disaggregation`.

```
/data/pd-disaggregation/pd-disaggregation.setup_modelservice_NA_NA_1_4_3_4
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
    └── vllm-benchmark_1758657030-setup_modelservice_NA_NA_1_4_3_4-run_1_10_llm-d-70b-instruct
        ├── benchmark_report,_vllm-infqps-concurrency1-Llama-3.1-70B-Instruct-20250923-195948.json.yaml
        ├── random_concurrent_treatment_run_1_10.yaml
        ├── results.json
        ├── stderr.log
        └── stdout.log
    └── vllm-benchmark_1758657030-setup_modelservice_NA_NA_1_4_3_4-run_8_80_llm-d-70b-instruct
    └── vllm-benchmark_1758657030-setup_modelservice_NA_NA_1_4_3_4-run_32_320_llm-d-70b-instruct
    # ... and so on
```

Feel free to use the [Config Explorer](../config_explorer/) to explore the data.

```
pip install ./config_explorer
streamlit run ./config_explorer/Capacity_Planner.py
```

The UI should be up and running. You can get a preview of the Config Explorer [here](https://drive.google.com/file/d/1lzdj2P65yhQG3w5gsVxULkTqSYwMH3ec/view?usp=sharing).
