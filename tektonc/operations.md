Based on experiments with Tekton, some basic composable operations might include:  

## Tooling

Operations to install tooling and to configure the environment. This might include installing and configuring the cluster; for example, a gateway provider (istio, kgateway, gke), LWS, Tekton, etc.
It might also include installing runtime tooling such as llmdbench, helm, yq, git, kubectl, oc, etc.

## Stack Creation

Operations to create elements of the model stack -- gateway, GAIE, and model servers.

To delpoy each stack, a unique DNS compatible identifier (`model_label`) is required. It serves two purposes:

(a) For each model service, a GAIE deployment is created. The  `InferencePool` identifies the pods of the model service using a set of match labels. Typically, the `llm-d.ai/model` label is used for this. Its value must be unique across all model services in the namespace. The `model_label` can be used for this.

(b) At the level of the Gateway, there must be a means to distinguish requests for one model service vs. another. For most workload generators, the simplest mechanism is to modify the request path by inserting a model specific prefix in the path. This prefix must be unique to the instance of the deployed model. Again, the `model_label` can be used for this (in an `HTTPRoute`).

### Operation: `deploy_gateway`

**Description:** 

Installs a gateway pod into a namespace.

Notes: A gateway pod can be used for multiple namespaces. This requires additional configuration and is ignored for now. It is assumed that if a model is deployed

**Inputs**: 

- *namespace*
- *release_name* - Helm release name
- *helm_chart_values* - (default: ?)
- *helm_chart* - (default: `llm-d-infra/llm-d-infra`)
- *helm_chart_version* - (default: `none` (latest))
- *helm_chart_repository_url* - (default: `https://llm-d-incubation.github.io/llm-d-infra/`)

**Outputs**:

_ _name_ - name of gateway created
- _serviceUrl_ - endpoint (incl. port) to be used by requests

### Operation: `deploy_gaie`
    
**Inputs**: 

- *namespace*
- *model_label* - used to configure `InferencePool` match labels.
- *release_name* - Helm release name
- *helm_chart_values* - [samples](https://github.com/llm-d/llm-d/tree/main/guides/prereq/gateway-provider/common-configurations)
- *helm_chart* - (default: `oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool`)
- *helm_chart_version* (default: `v1.0.1`)
- *helm_chart_repository_url* (default: `none`)
- *helm_overrides* - list of fields to set? values file to apply? *model_label* used here?

**Outputs**:

### Operation: `deploy_model`

**Inputs**:

**Outputs**:

### Operation: `create_httproute`

**Description:** 

Create an `HTTPRoute` object to match requests to a Gateway to the GAIE `InferencePool` (and hence to the model service Pods). One HTTPRoute can be created per stack. Alternatively, a single `HTTPRoute` can configure multiple mappings (currently required for Istio).

**Inputs**:

- *namespace*
- *manifest* - Requires Gateway *name* and InferencePool *name* and a *model_label*

**Outputs**:

### Operation: `download_model`

**Description:** 

Downloads model from HF to a locally mounted disk.

**Inputs**:

- *model*
- *HF_TOKEN*
- *path* - location to which the model should be downloaded

**Outputs**:

- *endpoint* - url for sending requests from within the cluster

## Run Workloads

### Operation: `create_workload_profile`

**Description**:

Modify a workload profile template for a particular execution. The profile format is specific to workload generator (harness) type. Should this be part of **run_workload**?

**Inputs**:

- **harness_type**
- **workload_profile_template** - workload profile (yaml) or location of profile template
- **changes** - name/path/value information to modify template; In addition to the workload parameters, this includes:

  - **stack_endpoint** - endpoint to be used to send requests
  - **model** - HF model name

**Outputs**:

- **workload_profile** - yaml string or url to location

### Operation: `run_workload`

**Description**:

Configure and run a workload generator (harness). On completion, results are saved to a locally mounted filesystem and are converted to a universal format. Should conversion be a separate operation?

**Inputs**:

- **harness_type**
- **workoad_profile** - workload profile (yaml)
- **HF_TOKEN** - required by some generators (`vllm-benchmark`) 
- **path** - path to where results should be saved

**Outputs**:

### Operation: `record`

**Description**:

Record configuration of stack and workload. Should this be part of **run_workload**?

**Inputs**:

- All inputs from `deploy_gaie`, `deploy_model`, `create_httproute`, and `run_workflow`

**Outputs**:

- list of paths?

### Operation: `upload`

**Description**:

Copy results from a locally mounted files to remote location. Should there be one operation per target type?

**Inputs**:

- list of paths to upload
- target_details

    - this is specific to the target type, for example for s3 compatible bucket:
    - *AWS_ACCESS_KEY_ID*
    - *AWS_SECRET_ACCESS_KEY*
    - *s3_endpoint*
    - *s3_bucket*
    - *target_object_name*

**Outputs**: