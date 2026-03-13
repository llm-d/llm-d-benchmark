# Standup Phase

The standup module deploys the llm-d stack to a Kubernetes cluster. It consists of numbered steps that execute sequentially (global steps) or in parallel across model stacks (per-stack steps).

## Module Structure

```text
standup/
    __init__.py
    preprocess/                         Scripts mounted as ConfigMaps in vLLM pods
        set_llmdbench_environment.py    Runtime environment setup for vLLM pods
        standalone-preprocess.py        Preprocess for standalone deployments
    steps/
        __init__.py                     Step registry (get_standup_steps)
        step_00_ensure_infra.py         Validate dependencies and cluster connectivity
        step_02_admin_prerequisites.py  Admin prerequisites (CRDs, gateway, LWS)
        step_03_workload_monitoring.py  Workload monitoring, node resource discovery
        step_04_model_namespace.py      Model namespace (PVCs, secrets, download)
        step_05_harness_namespace.py    Harness namespace (PVC, data access)
        step_06_standalone_deploy.py    Standalone vLLM deployment
        step_07_deploy_setup.py         Helm repos and gateway infrastructure
        step_08_deploy_gaie.py          GAIE inference extension deployment
        step_09_deploy_modelservice.py  Modelservice deployment (helmfile + LWS)
        step_10_smoketest.py            Endpoint health and model validation
```

## Step Details

### Step 00: Ensure Infrastructure (Global)

Validates that all required tools are available and the cluster is reachable:

- Checks for `kubectl`, `helm`, `helmfile`, `python3`
- Resolves cluster connectivity (kubeconfig, context, API server URL)
- Detects platform type (OpenShift, Kind, Minikube, vanilla Kubernetes)
- Builds the shared `CommandExecutor`

### Step 02: Admin Prerequisites (Global)

Creates cluster-level resources that require admin privileges:

- Installs gateway provider (kgateway or Istio) via helmfile
- Installs LeaderWorkerSet (LWS) CRD if needed
- Creates deploy and harness namespaces with labels
- Skipped when `--non-admin` is passed

### Step 03: Workload Monitoring (Global)

Configures monitoring and discovers node resources:

- Applies OpenShift workload monitoring ConfigMap (OpenShift only)
- Discovers GPU/accelerator resource names from cluster nodes
- Discovers network resources (RDMA) from cluster nodes
- Stores discovered values in `ExecutionContext`

### Step 04: Model Namespace (Per-stack)

Sets up the model namespace for each stack:

- Applies namespace, ServiceAccount, RBAC, and secrets YAML
- Creates model storage PVC and extra PVCs
- Creates or reuses HuggingFace token secret
- Launches model download Job (waits for completion or skips if model exists)

### Step 05: Harness Namespace (Per-stack)

Sets up the harness/benchmark namespace:

- Creates harness workload PVC
- Deploys data access pod and service
- Creates ConfigMap with preprocess scripts from `standup/preprocess/`

### Step 06: Standalone Deploy (Per-stack)

Deploys vLLM as standalone Kubernetes Deployments (skipped for modelservice method):

- Applies standalone Deployment YAML
- Applies standalone Service YAML
- Applies standalone PodMonitor YAML (if non-empty)
- Waits for pods to reach Ready state
- Records deployed endpoint in context

### Step 07: Deploy Setup (Per-stack)

Sets up Helm infrastructure for modelservice deployments (skipped for standalone):

- Adds Helm repositories
- Runs gateway provider helmfile (if rendered)
- Runs main infrastructure helmfile

### Step 08: Deploy GAIE (Per-stack)

Deploys the GAIE inference extension (skipped for standalone):

- Applies HTTPRoute YAML
- Deploys GAIE via main helmfile with GAIE values
- Waits for GAIE pods to be ready

### Step 09: Deploy Modelservice (Per-stack)

Deploys the modelservice stack (skipped for standalone):

- Runs helmfile with modelservice and infrastructure values
- Handles LeaderWorkerSet (LWS) deployments for expert parallelism
- Creates WVA namespace and deploys Workload Variant Autoscaler (if enabled)
- Deploys Prometheus adapter (if enabled)
- Waits for decode and prefill pods to reach Ready state
- Records deployed endpoints in context

### Step 10: Smoketest (Per-stack)

Validates that the deployed model is serving correctly:

- Creates an ephemeral curl pod in the deploy namespace
- Sends inference requests to the model endpoint
- Validates that the response contains expected model output
- Cleans up the curl pod after validation

### Step 11: Management (Global)

Post-standup summary and management operations.

## Deployment Methods

### Standalone

Steps 00 -> 02 -> 03 -> 04 -> 05 -> **06** -> 10

Creates direct Kubernetes Deployments and Services. Simpler but without gateway routing, autoscaling, or inference scheduling.

### Modelservice

Steps 00 -> 02 -> 03 -> 04 -> 05 -> **07 -> 08 -> 09** -> 10

Deploys via Helm charts with full llm-d infrastructure including gateway, GAIE inference extension, modelservice, and optional LWS and WVA.

## Preprocess Scripts

The `preprocess/` directory contains Python scripts that are mounted into vLLM pods as ConfigMaps:

- **`set_llmdbench_environment.py`** -- Discovers runtime environment (UCX transport, NIXL settings, network interfaces) and writes environment variables to `$HOME/llmdbench_env.sh`. Sourced by vLLM pods at startup.
- **`standalone-preprocess.py`** -- Simplified preprocess for standalone deployments.

## Usage

```bash
# Full standup
llmdbenchmark --spec config/specification/guides/inference-scheduling.yaml.j2 standup

# Specific steps only
llmdbenchmark --spec ... standup -s 4-6,10

# Standalone deployment
llmdbenchmark --spec ... standup -t standalone

# Modelservice deployment (default)
llmdbenchmark --spec ... standup -t modelservice

# Parallel stacks (for multi-model experiments)
llmdbenchmark --spec ... standup --parallel 8
```
