# Kubernetes Benchmark Launcher

This guide explains how to run benchmarks on LLM deployments.
It uses [fmperf](https://github.com/fmperf-project/fmperf), specifically [fmperf's run_benchmark](https://github.com/fmperf-project/fmperf/blob/main/fmperf/utils/Benchmarking.py#L48)
The environment vars are configured via a [configmap](./resources/workload-configmap.yaml).
This example runs LLM benchmarks in a Kubernetes cluster using a two-level job structure:

1. A launcher job that sets up the environment and configuration
2. A benchmark job that performs the actual load testing

The launcher job (`fmperf-benchmark`) creates and monitors a benchmark job (`lmbenchmark-evaluate-{timestamp}`) that runs the actual load tests against your model. The launcher job will wait for the benchmark job to complete before exiting.

## Prerequisites

1. A running Kubernetes cluster
2. An existing model deployment with an accessible inference endpoint
3. Hugging Face Token secret

### Using Hugging Face Token secret

Some models and benchmarks require authentication with Hugging Face. Even if you already have the model served,
evaluation code will reach out to HuggingFace to download tokenizer files if it cannot find them locally.
The benchmark code supports an HF_TOKEN_SECRET

Create a Kubernetes secret with your Hugging Face token:
   ```bash
   kubectl create secret generic huggingface-secret \
     --from-literal=HF_TOKEN=your_hf_token_here \
     -n fmperf
   ```

Set the `HF_TOKEN_SECRET` environment variable in the `job.yaml` file:
   ```yaml
    - name: HF_TOKEN_SECRET
      value: "huggingface-secret"  # Name of your secret
   ```

This method is secure and doesn't expose your token in the pod's environment variables.

## Compare Multiple LLM Deployments

FMPerf supports comparing two different LLM deployments side-by-side
(for example, comparing a vanilla deployment with an optimized version like llm-d).

For complete instructions on running comparative benchmarks, please see:
- [Compare-README.md](Compare-README.md) - Step-by-step guide for running comparison benchmarks

The comparison workflow allows you to:
1. Run benchmarks against two different LLM deployments
2. Collect results from both
3. Generate side-by-side visualizations and statistics
4. Quantify performance improvements

## Important Notes

- The RBAC permissions in `rbac.yaml` are configured for:
  - Service Account: `fmperf-runner`
  - Namespace: `fmperf`
- Keep these values unchanged unless you update the RBAC configuration accordingly
- PVC is expected to be `fmperf-results-pvc`, as is named in the pvc definition file.
- The benchmark results will be stored in the PVC mounted at `/requests`
- The launcher job will wait for the benchmark job to complete before exiting

## Run the Benchmarks for a Single Model Service

0. Edit [workload-configmap.yaml](./resources/workload-configmap.yaml) and job definition to match your requirements. The benchmark job is configured in [job.yaml](./job.yaml). Key configuration elements include:

- **Service Endpoint**: Update `FMPERF_ENDPOINT_URL` with your baseline service endpoint
- **Model Configuration**: Set `FMPERF_WORKLOAD_FILE` to point to your baseline workload configuration
- **Stack Configuration**: Set `FMPERF_STACK_NAME` and `FMPERF_STACK_TYPE` for your baseline deployment
- **Results Directory**: Set `FMPERF_RESULTS_DIR` to `/requests` (mounted to baseline-results-pvc)

1. Create the PVC, serviceaccount, workload-configmap, and rbac for the fmperf-runner job:

   ```bash
   kubectl apply --kustomize resources
   kubectl apply resources/pvc.yaml
   ```
2. Create and monitor the launch and evaluate job.

   ```bash
   kubectl apply -f job.yaml

   # Watch the launcher job
   kubectl get jobs -n fmperf fmperf-benchmark -w
   
   # Watch the benchmark job
   kubectl get jobs -n fmperf lmbenchmark-evaluate-* -w
   ```

## Retrieve and Analyze Results

The benchmark results are saved in the PVC mounted at `/requests`. To access and analyze the results:

1. Create the retriever pod:
   ```bash
   kubectl apply -f retrieve.yaml
   ```

2. Wait for the pod to be ready and list contents:
   ```bash
   kubectl exec -n fmperf results-retriever -- ls -la /requests
   ```

3. Copy the results to your local machine:
   ```bash
   mkdir -p ./fmperf-results
   kubectl cp fmperf/results-retriever:/requests/ ./fmperf-results/

   # upon successful copy, delte the retriever pod
   kubectl delete pod results-retriever -n fmperf
   ```

## Analyzing Results

The benchmark results can be analyzed using the provided Python script in the `analyze-results` directory. The script generates visualizations and statistics for latency and throughput metrics.

1. Install required packages:
   ```bash
   python -m venv venv && source venv/bin/activate
   pip install pandas matplotlib seaborn grip
   ```

2. Run the analysis script:
   ```bash
   python analyze-results/analyze_results.py --results-dir fmperf-results
   ```

The script will create a `plots` directory and generate:
- `plots/latency_analysis.png`: Shows latency metrics across different QPS levels
- `plots/throughput_analysis.png`: Shows throughput and token count metrics
- `plots/README.md`: Contains detailed descriptions of the plots

#### View README with grip (recommended)

[Grip](https://github.com/joeyespo/grip) renders Markdown files with GitHub styling for better visualization:

```bash
# Install grip if needed
pip install grip

cd ./compare-results/analysis/plots

# Generate HTML and view in browser (--browser opens it automatically)
grip README.md --browser
```
