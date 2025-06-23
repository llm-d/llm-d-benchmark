# llm-d-benchmark Workflow

This workflow provides a way to run [`setup/run.sh`](../setup/run.sh) benchmark experiments and analysis logic.
It works with both standard Kubernetes clusters and OpenShift, and assumes the llm-d and/or vLLM stack is already deployed.

## Overview

The Kubernetes workflow consists of these main components:

1. **`benchmark-job.yaml`** - Runs the benchmark experiment portion of `setup/run.sh`, [`fmperf-llm-d-benchmark.py`](../workload/harnesses/fmperf-llm-d-benchmark.py)
2. **`analysis-job.yaml`** - Runs the analysis portion of `setup/run.sh`, [`fmperf-analyze_results.py`](../analysis/fmperf-analyze_results.py)

This workflow is a simplified Kubernetes-native version of `setup/run.sh`, which provides command-line options for model selection,
scenario configuration, and benchmark execution. It is meant to run benchmark experiments on already-existing llm-d and/or vLLM deployments.

## Key Features

- **Kubernetes & OpenShift Compatible**: Uses `runAsNonRoot`, drops all capabilities, restricted security contexts
- **Main Project Integration**: Uses the same scripts, profiles, and scenarios as [`setup/run.sh`](../setup/run.sh)
- **Pure Kubernetes**: Everything runs in jobs, no local execution required
- **Analysis Integration**: Includes the same analysis capabilities as the main project

## Prerequisites

1. **Kubernetes/OpenShift cluster** with `kubectl` configured
2. **llm-d stack and/or standalone vLLM** already deployed and accessible
3. **Required Kubernetes resources** (see Setup section)

## Setup

### Create Namespace and Resources (PVC, RBAC, and configmaps)

```bash
kubectl create namespace llm-d-benchmark

# Update resources/benchmark-env.yaml with your cluster configuration
# Update resources/benchmark-workload-configmap.yaml with your workload settings
kubectl apply -k resources/

# You will now have the PVC, configmaps, and RBAC necessary to proceed
```

## Configuration

Before running the workflow, customize the configuration files to match your deployment and benchmarking requirements.

**📖 For detailed configuration instructions, see: [Configuration Guide](quickstart-config.md)**

### Quick Configuration Checklist

Before proceeding, ensure you have:

1. ✅ **Updated `resources/benchmark-env.yaml`** with your endpoint URL and stack type
2. ✅ **Updated `resources/benchmark-workload-configmap.yaml`** with your model name and scenarios
3. ✅ **Created the HuggingFace token secret** (if using gated models)

## Running the Workflow

### Run the Experiment Job

```bash
kubectl apply -f benchmark-job.yaml
```

### Monitor the Experiment

```bash
# Follow the logs
kubectl logs -f job/benchmark-run -n llm-d-benchmark

# Check job status
kubectl get job benchmark-run -n llm-d-benchmark
```

### Run Analysis After Experiment Completes

Wait for the experiment job to complete successfully, then run the analysis:

```bash
# Verify experiment completed successfully
kubectl get job benchmark-run -n llm-d-benchmark

# Run analysis job
kubectl apply -f analysis-job.yaml
```

### Monitor the Analysis

```bash
# Follow the analysis logs
kubectl logs -f job/benchmark-analysis -n llm-d-benchmark
```

### Retrieve Results

The analysis job will generate plots and statistics in the shared PVC. You can access them using the provided retrieve script:

```bash
kubectl apply -f retrieve.yaml
kubectl logs job/retrieve-results -n llm-d-benchmark
```

Or copy the results directly to your local system:

```bash
# Create local directory for results
mkdir -p ./benchmark-results

# Copy results from PVC to local system using the retrieve pod
kubectl cp llm-d-benchmark/results-retriever:/requests ./benchmark-results/

# Clean up retriever pod
kubectl delete pod results-retriever -n llm-d-benchmark
```

## Output Files

After successful completion, you'll find:

**In the PVC** (and locally in `./benchmark-results/` after `kubectl cp`):

- **Raw benchmark data**: 
  - PVC: `/requests/<stack-name>/`
  - Local: `./benchmark-results/<stack-name>/`
  - Contains: CSV files with benchmark results
- **Analysis plots**:
  - PVC: `/requests/analysis/plots/`
  - Local: `./benchmark-results/analysis/plots/`
  - Contains: PNG files with visualizations
    - `latency_analysis.png` - Latency metrics across QPS levels
    - `throughput_analysis.png` - Throughput and token count analysis
    - `README.md` - Description of the plots
- **Statistics**:
  - PVC: `/requests/analysis/data/stats.txt`
  - Local: `./benchmark-results/analysis/data/stats.txt`
  - Contains: Summary statistics

## Viewing the Analysis Results

The generated `README.md` in the plots directory contains embedded images showing the analysis visualizations. To view it properly with the plots displayed:

```bash
# Create a virtual environment and install grip
python -m venv venv && source venv/bin/activate && pip install grip

# View the analysis README with plots in your browser
grip benchmark-results/analysis/plots/README.md --browser
```

This will open a rendered view of the analysis documentation with all plots displayed inline.

## Cleanup

```bash
# Delete jobs
kubectl delete job benchmark-run benchmark-analysis -n llm-d-benchmark

# Delete all resources (optional)
kubectl delete namespace llm-d-benchmark
```
