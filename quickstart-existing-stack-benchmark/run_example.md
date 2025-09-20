# `llm-d-benchmark` Example

***Benchmarking an Existing Stack***


## Goal

A simple, minimal example of using `llm-d-benchmark` to test an already deployed `llm-d` stack with `inference-perf`.  

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

You will need a `yaml` specification to tell `inference-perf` how to generate the _Workload_ that would be used to benchmark your stack. `inference-perf` will generate prompts (AKA a _Data Set_) with timing (AKA _Load_).

Several workload examples are available under `llm-d-benchmark/workload/profiles/inference-perf`. We demonstrate with `shared_prefix_synthetic`.

<details>
<summary>Click to view `shared_prefix_synthetic.yaml.in`</summary>

For example, `shared_prefix_synthetic.yaml.in`:
```yaml
load:
  type: constant
  stages:
  - rate: 2
    duration: 50
  - rate: 5
    duration: 50
  - rate: 8
    duration: 50
  - rate: 10
    duration: 50
  - rate: 12
    duration: 50
  - rate: 15
    duration: 50
  - rate: 20
    duration: 50
api:
  type: completion
  streaming: true
server:
  type: vllm
  model_name: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
  base_url: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
  ignore_eos: true
tokenizer:
  pretrained_model_name_or_path: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_TOKENIZER
data:
  type: shared_prefix
  shared_prefix:
    num_groups: 32                # Number of distinct shared prefixes
    num_prompts_per_group: 32     # Number of unique questions per shared prefix
    system_prompt_len: 2048       # Length of the shared prefix (in tokens)
    question_len: 256             # Length of the unique question part (in tokens)
    output_len: 256               # Target length for the model's generated output (in tokens)
report:
  request_lifecycle:
    summary: true
    per_stage: true
    per_request: true
storage:
  local_storage:
    path: /workspace
```
</details>

If you want to create your own `inference-perf` profile then save your custom `yaml` specification with a `.yaml.in` suffix in the same directory.

⚠️ Unless you know exactly what you are doing, you should only edit the `load` and `data` sections. `load` tell `inference-perf` how many sub-test ("_stages_") to run; each stage has a desired QPS (Queries Per Second) and duration. `data` defines how to create the _DataSet_; the configuration parameters are different for each `type`.


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
export LLMDBENCH_DEPLOY_METHODS="infra-inference-scheduling-inference-gateway"
export LLMDBENCH_HARNESS_NAME="inference-perf"
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
run.sh \
  -c "$(realpath ./myenv.sh)"  `# use full path` \
  -w "shared_prefix_synthetic" `# <name of the Harness Profile>.yaml.in` \
  # -s 10000  # uncomment to setup timeout on command line. 
  
```

Wait for test completion... ⏳ ... ⏳ ... ⏳ ...
☕ ...
⏳ ... ⏳ ...

  _Note_: Each stage in the test may run longer than the duration specified in the `yaml.in` file. `inference perf` sets the _desired_ timing for each request, but waits until all requests complete (succeed/fail). There is no timeout by `inference perf` itself; however, you can set timeouts in your `llm-d` stack.


### 6. 🔍 Examine Results

The results would be stored under the given _Work Directory_.
* `results` holds the numeric results and logs (each experiment will have a unique sub-directory)
* `analysis` holds the plots (each experiment will have a unique sub-directory)
* Other directories hold detailed information on the last run (e.g., the `.yaml` files used).

The results are not lost if the local `run.sh` times out or the connection to cluster is lost.
`run.sh` creates a pod to access the _Harness PVC_ called `access-to-harness-data....`.
You can `oc rsh` or `kubectl exec -it` into the pod and run `bash` to view the results under the `/requests` directory.
You can use `oc rsync` or `kubectl copy` to fetch the results.

  <details>
  <summary>Click to view bash code snippet</summary>

  Find access pod name, e.g.,   
  ```bash
  $ oc get pods -l app=llm-d-benchmark-harness -o name

  pod/access-to-harness-data-vllm-p2p-70b-chart-llama-3-70b-instruct-storage-claim
  ```
  
  List latest results (`kubectl` uses slightly different syntax)
  ```bash 
  oc rsh pod/access-to-harness-data-vllm-p2p-70b-chart-llama-3-70b-instruct-storage-claim ls -lrt /requests | tail -3

  drwxr-sr-x. 3 root       1001020000 13 Aug  5 18:17 inference-perf_1754416561_inference-gateway-70b-instruct
  drwxr-sr-x. 3 root       1001020000 13 Aug  5 18:39 inference-perf_1754417987_inference-gateway-70b-instruct
  drwxr-sr-x. 3 root       1001020000 13 Aug  5 19:02 inference-perf_1754419311_inference-gateway-70b-instruct
  ```
  
  Fetch the results (`kubectl` uses slightly different syntax)
  ```bash
  oc rsync access-to-harness-data-vllm-p2p-70b-chart-llama-3-70b-instruct-storage-claim:/requests/inference-perf_1754419311_inference-gateway-70b-instruct  /tmp --no-perms
  ```
  </details>
