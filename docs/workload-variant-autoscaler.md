# Workload Variant Autoscaler Integration

`llm-d-benchmark` provides the opportunity to deploy models with an autoscaler, called `workload-variant-autoscaler`.
For information about *how* the autoscaler works, please be sure to visit their documentation found [here](https://github.com/llm-d-incubation/workload-variant-autoscaler). In this document, we will refer to `workload-variant-autoscaler` as `WVA`.

## How to Deploy a Model with WVA

The simplest way to deploy a model that takes advantage of `WVA` is through the flag `-u/--wva`.

For example, we can easily standup a model that will take advantage of autoscaling via `WVA` by simply appending the aforementioned `WVA` flag:

    - ./setup/standup.sh -p llm-d-test-exp -m Qwen/Qwen3-0.6B -c inference-scheduling --wva

Here is a summary of what will occur in that command:

- A model will be stood up and all underlying infra will be provisioned. In this case it is `Qwen/Qwen3-0.6B` - and it will be deployed via the `inference-scheduling` well-lit-path - this is something that is **not** unique, but business as usual.

- `WVA` will either be *installed* or will be idempotent in the `WVA` *controller namespace*  (*llm-d-autoscaler* being the default) depending on if it already exists on the cluster. Do note, that it is actually possible to have multiple installations of `WVA` on a cluster in seperate namespaces - which one you target is dependent on the `namespace` that is configured within the `setup/env.sh`. As part of this process, we configure `Prometheus Adapters` to allow metrics from `model` to `WVA` controller to flow naturally.

- `WVA` model specific components (hpa va servicemonitor vllm-service) will be created in the `model namespace` - in this case, `llm-d-test-exp`.

## How to Undeploy a Model that uses WVA

There is no difference here, simply run `teardown.sh` as per usual with no additional flags for `WVA`. But there a few things you should understand:

- `teardown.sh` will remove all model specific resources, including the `WVA` model specific resources.
- `teardown.sh` will NOT remove the `WVA` controller from the `llm-d-autoscaler` namespace (or from another namespace) - this is done purposefully as to not interrupt other jobs, since many models can target a single instance of the `WVA` controller.

## How to Run Workloads on a Model that uses WVA

There is no difference here, and there is no additional `WVA` information needed here. Simply run `run.sh` as per usual - with no additional flags for `WVA`. For an example benchmarking scenario see the below real usecase.

---

## Initial Benchmarking of WVA by Leveraging LLM-D-Benchmark

Benchmark tests have been conducted on an `H100`.

## Reproducibility

To reproduce the experiments run during the intial benchmarking of `WVA` you may follow the below guide - although both sub-sections may look identical
at first glance, but there are some subtleties. This guide assumes that you have cloned [llm-d-benchmark](github.com/llm-d/llm-d-benchmark) and have installed
it on your local machine, or system you are *driving* the experiments.

### Without WVA

0. Create a namespace in where you wish to install the `llm-d` stack, we will use this namespace a lot throughout these steps.

1. Deploy a full `llm-d` stack for a target model, in this case, `meta-llama/Llama-3.1-8B`, using the `inference-scheduling` well-lit-path.

    - Template Command: `./setup/standup.sh -p <namespace> -m <model id> -c <well-lit-path>`

        - Example Populated Command: `./setup/standup.sh -p llm-d-vezio -m meta-llama/Llama-3.1-8B -c inference-scheduling`

2. Run a workload using the `guidellm` `harness`  and `chatbot_synthetic` `workload profile` against an existing `llm-d` stack, using the `inference-scheduling` well-lit-path.

    - Template Command: `./run.sh -l <harness> -w <workload profile> -p llm-d-vezio  -m <model id> -c <well-lit-path>`

        - Example Populated Command: `./run.sh -l guidellm -w chatbot_synthetic -p llm-d-vezio -m meta-llama/Llama-3.1-8B -c inference-scheduling`

3. Repeat `Step 2` to rerun a workload, in our case we reran `Step 2`, a total of 4 times.

4. Collect results from the `analysis` directory that is provided in the logs of the `run.sh` command. You can see a detailed and granular report in the `results` directory if needed.

    - Make sure you seperate these results into a seperate directory from the WVA experiment - they are not the best named - we will change this - otherwise it will be hard to tell what is what!

5. Tear down the infrastructure (this will not delete the namespace, but will completely purge *all* resources for the model, effectively leaving an empty namespace - it will NOT touch cluster-wide resources.)

    - Template Command: `./setup/teardown.sh -p <namespace> -d -c <well-lit-path>`

        - Example Populated Command: `./setup/teardown.sh -p llm-d-vezio -d -c inference-scheduling`

### With WVA

0. Create a namespace (OR reuse the now *cleaned* namespace from the previous experiment) where you wish to install the `llm-d` stack, we will use this namespace a lot throughout these steps.

1. Deploy a full `llm-d` stack for a target model, that will be scaled by `WVA`, in this case, `meta-llama/Llama-3.1-8B`, using the `inference-scheduling` well-lit-path.

    - Template Command: `./setup/standup.sh -p <namespace> -m <model id> -c <well-lit-path> --wva`

        - Example Populated Command: `./setup/standup.sh -p llm-d-vezio -m meta-llama/Llama-3.1-8B -c inference-scheduling --wva`

2. Run a workload using the `guidellm` `harness`  and `chatbot_synthetic` `workload profile` against an existing `llm-d` stack, using the `inference-scheduling` well-lit-path.

    - Template Command: `./run.sh -l <harness> -w <workload profile> -p llm-d-vezio  -m <model id> -c <well-lit-path>`

        - Example Populated Command: `./run.sh -l guidellm -w chatbot_synthetic -p llm-d-vezio -m meta-llama/Llama-3.1-8B -c inference-scheduling`

3. Repeat `Step 2` to rerun a workload, in our case we reran `Step 2`, a total of 4 times.

4. Collect results from the `analysis` directory that is provided in the logs of the `run.sh` command. You can see a detailed and granular report in the `results` directory if needed.

    - Make sure you seperate these results into a seperate directory from the non WVA experiment - they are not the best named - we will change this - otherwise it will be hard to tell what is what!

5. Tear down the infrastructure (this will not delete the namespace, but will completely purge *all* resources for the model, including the `wva` variant resources, effectively leaving an empty namespace - it will NOT touch cluster-wide resources.)

    - Template Command: `./setup/teardown.sh -p <namespace> -d -c <well-lit-path>`

        - Example Populated Command: `./setup/teardown.sh -p llm-d-vezio -d -c inference-scheduling`

### Where is the Guidellm chatbot_synthetic Workload Profile Defined?

You can find the actual location of the `chatbot_synthetic` workload profile [here](https://github.com/llm-d/llm-d-benchmark/blob/main/workload/profiles/guidellm/chatbot_synthetic.yaml.in) - as well as the Guidellm [documentation](https://github.com/vllm-project/guidellm/tree/7666c658460bc34abe3cc821d3ca072cfd39074a).

For your convenience I have copied the profile below - notice, our automation will autopopulate the `target` and `model` fields:

```yaml
target: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
request_type: text_completions
profile: constant
rate: [1,2,4,8]
max_seconds: 120
data:
prompt_tokens_min: 10
prompt_tokens_max: 8192
prompt_tokens: 4096
prompt_tokens_stdev: 2048
output_tokens_min: 10
output_tokens_max: 2048
output_tokens: 1024
output_tokens_stdev: 512
samples: 1000
```

This will workload profile will generate synthetic data - there is still a PR open describing a feature to support the ability to supply the ShareGPT dataset directly, that is currently not functional, [source here](https://github.com/vllm-project/guidellm/pull/305).

### What the Commands Do

#### Standup

- For all options see the `manual` via `-h/--help`
- Ensures gateway provider is installed, otherwise it will install it (`istio` is the default, and what is used here in the experiments (1.28.1))
- Ensures workload monitoring is prepared and configured
- Ensures the model namespace is prepared and configured
- Ensures harness namespace is prepared and configured
- Ensures infra-llmdbench and modelservice are deployed, (and configures WVA if and only if it is enabled)
- Runs a smoketest against the exposed model inference service

#### Run

- For all options see the `manual` via `-h/--help`
- Automatically detects the `llm-d stack entrypoint`, for example, `"http://infra-llmdbench-inference-gateway-istio.llm-d-vezio.svc.cluster.local:80"`
- Renders workload profile templates
- Starts a harness pod for the target model
- Runs workload via the harness pod using the endpoint automatically discovered
- Copies the results and analysis from the pod to your local machine, or system you are driving the experiments
- Cleans up and removes harness pod

Tear down the infrastructure (this will not delete the namespace, but will completely purge *all* resources for the model, including the `wva` variant resources, effectively leaving an empty namespace - it will NOT touch cluster-wide resources.)

#### Teardown

- For all options see the `manual` via `-h/--help`
- Tears down model infrastructure of a given namespace - but it will *not* delete the namespace
- Removes all model resources in the namespace provided, including the `wva` variant resources
