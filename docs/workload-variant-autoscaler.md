# Workload Variant Autoscaler Integration

`llmd-benchmark` provides the opportunity to deploy models with an autoscaler, called `workload-variant-autoscaler`.
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

There is no difference here, and thee is no additional `WVA` information needed here. Simply run `run.sh` as per usual - with no additional flags for `WVA`.