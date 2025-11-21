# `llm-d-benchmark` Example

***Benchmarking Interactively***


## Goal

A simple, minimal example of using `llm-d-benchmark` to interactively test against an already deployed `llm-d` stack with `guidellm`.

>  **Note:** For ease of presentation, the example assumes an OpenShift cluster and uses `oc`. For a Kubernetes cluster replace `oc` by `kubectl`.


## Preliminaries


### 📦 Setup the `llm-d-benchamrk` repository

```
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark
./setup/install_deps.sh
```

See [list of dependencies](https://github.com/deanlorenz/llm-d-benchmark?tab=readme-ov-file#dependencies).


### Prepare an `llm-d` cluster

Set up the stack for benchmarking. Since you are starting from an existing stack, you may need to restart some pods to create a clean baseline. For this simple example, if you want to compare different setups (e.g., various `epp` configurations) then you have to set up each configuration manually and rerun the example for each.

In this example, the benchmark sends requests to an `infra-inference-scheduling-inference-gateway` endpoint. Replace `infra-inference-scheduling-inference-gateway` with the name your inference gateway service. Alternatively, use a pod name if you want to benchmark a single `vllm` instead.


## Benchmarking Steps


### 1. Prepare workload specification (profile)

You will need a `yaml` specification to tell `guidellm` how to generate the _Workload_ that would be used to benchmark your stack. `guidellm` will generate prompts (AKA a _Data Set_) with timing (AKA _Load_).

Several workload examples are available under `llm-d-benchmark/workload/profiles/inference-perf`. We demonstrate with `shared_prefix_synthetic`.

<details>
<summary>Click to view `shared_prefix_synthetic.yaml.in`</summary>

For example, `shared_prefix_synthetic.yaml.in`:
```yaml
target: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
request_type: text_completions
profile: constant
rate: [2,5,8,10,12,15,20]
max_seconds: 50
data:
  prefix_tokens: 2048
  prefix_count: 32
  prompt_tokens: 256
  output_tokens: 256
```
</details>

If you want to create your own `guidellm` profile then save your custom `yaml` specification with a `.yaml.in` suffix in the same directory.

⚠️ Unless you know exactly what you are doing, you should only edit the `rate` and `data` sections. `rate` tells `guidellm` how many sub-test to run; each sub-test has a rate and (max) duration. `data` defines how to create the _DataSet_; the configuration parameters are different for each `type`.


### 2. Log on into your cluster and namespace
Run `oc login ...`

Then, run
```bash
oc project <_namespace-name_>
```
or, if using `kubectl`
```bash
kubectl config set-context --current --namespace=<_namespace-name_>
```

### 3. Gather required parameters (mostly information about your `llm-d` stack)

* **Work Directory**:
  Choose a local work directory to save the results on your computer.

* **Harness Profile**:
  The name of your `.yaml.in` file _without the suffix_, e.g., `shared_prefix_synthetic`

* **PVC**:
  A Persistent Volume Claim for storing benchmarking results. Must be one of the available PVCs in the cluster.

  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  oc get persistentvolumeclaims -o name
  ```
  </details>


* **Hugging-Face Token [Optional]**:
  If none is specified then the `HF_TOKEN` used in the existing `llm-d` stack will be used.
  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  oc get secrets llm-d-hf-token -o jsonpath='{.data.*}' | base64 -d
  ```
  </details>

<!--
* **Namespace**:
  The K8S namespace / RHOS project being use.
  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  oc config current-context | awk -F / '{print $1}'
  ```
  </details>
-->

<!--
* **Model**:
  The exact model name of the LLM being served by your `llm-d` stack.

  <details>
  <summary>Click to view bash code snippet</summary>

  ```bash
  get oc get routes -l app.kubernetes.io/name=inference-gateway

  # note the HOST and PORT from the above command

  curl -s http://<HOST>:<PORT>/v1/models | jq '.data[].root'`
  ```
  </details>
-->

### 4. Create Environment Configuration File
Prepare a file `./myenv.sh` with the following content: (file name must have a `.sh` suffix)

```bash
# export LLMDBENCH_HF_TOKEN=<_your Hugging Face token_>

# Work Directory; for example "/tmp/<_namespace_>"
export LLMDBENCH_CONTROL_WORK_DIR="<_name of your local Work Direcotry_>"

# Persistent Volume Claim
export LLMDBENCH_HARNESS_PVC_NAME="<_name of your Harness PVC_>"

# This is a timeout (seconds) for running a full test
# If time expires the benchmark will still run but results will not be collected to local computer.
export LLMDBENCH_HARNESS_WAIT_TIMEOUT=3600
```


### 5. Call `run.sh`

`cd` into `llm-d-benchmark` root directory.

```bash
run.sh -t infra-inference-scheduling-inference-gateway
  -c "$(realpath ./myenv.sh)" -d
```

The execution will end with messages such as (illustrative example)

```
==> Fri Nov 21 11:23:29 EST 2025 - ./setup/run.sh - ℹ️  Harness was started in "debug mode". The pod can be accessed through "oc --kubeconfig /Users/msilva/data/tiered-prefix-cache/environment/context.ctx --namespace marcions exec -it pod/llmdbench-harness-launcher -- bash"
==> Fri Nov 21 11:23:29 EST 2025 - ./setup/run.sh - ℹ️  In order to execute a given workload profile, run "llm-d-benchmark.sh <[guidellm,inference-perf,inferencemax,nop,vllm-benchmark]> [WORKLOAD FILE NAME]" (all inside the pod "llmdbench-harness-launcher")
==> Fri Nov 21 11:23:29 EST 2025 - ./setup/run.sh - ℹ️  To collect results after an execution...
```
At this point, issue the command `oc exec -it llmdbench-harness-launcher -- bash`. Once inside, command line options for `llm-d-benchmark.sh` can be obtained.

```
msilva@marcios-ibm-mbp llm-d-benchmark % oc exec -it llmdbench-harness-launcher -- bash
root@llmdbench-harness-launcher:/workspace# llm-d-benchmark.sh --help
Usage: /usr/local/bin/llm-d-benchmark.sh -l/--harness [harness used to generate load (default=inference-perf, possible values guidellm,inference-perf,inferencemax,nop,vllm-benchmark]
                                         -w/--workload [workload to be used by the harness (default=random_concurrent.yaml, possible values ("ls /workspace/profiles/*/*.yaml")]
                                         -h/--help (show this help)
```

TBD
