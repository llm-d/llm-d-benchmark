# Teardown Phase

The teardown module reverses a standup by removing deployed resources, Helm releases, and cluster-scoped roles.

## Module Structure

```text
teardown/
    __init__.py
    steps/
        __init__.py                         Step registry (get_teardown_steps)
        step_00_preflight.py                Validate connectivity, load config
        step_01_uninstall_helm.py           Uninstall Helm releases and routes
        step_02_clean_harness.py            Clean harness resources
        step_03_delete_resources.py         Delete namespaced resources
        step_04_clean_cluster_roles.py      Clean cluster-scoped roles/bindings
        step_05_management_cleanup.py       Post-teardown management
```

## Operating Modes

### Normal Mode (default)

Removes only resources matching the deployment method:

- Helm releases matching the release name or model short names
- HTTPRoutes and Jobs in the deploy namespace
- Harness ConfigMaps, pods, and secrets
- Namespaced resources matching known patterns (deployments, services, pods, etc.)
- Preserves system ConfigMaps and the HuggingFace token secret

### Deep Mode (`--deep`)

Deletes **all** resources of every kind in both namespaces, leaving them empty:

- Iterates over all API resource types available in the namespace
- Deletes every resource found (deployments, services, configmaps, secrets, PVCs, etc.)
- Used for complete cleanup when redeploying from scratch

## Step Details

### Step 00: Preflight (Global)

Validates cluster connectivity and loads teardown configuration:

- Resolves cluster connection (kubeconfig, context)
- Loads plan config from rendered stacks
- Sets namespace and release from config or CLI overrides
- Errors if required config values (namespace, release) are missing

### Step 01: Uninstall Helm (Global, Modelservice only)

Uninstalls Helm releases and cleans up routes:

- Collects model labels from rendered stack configs for release matching
- Lists all Helm releases in the deploy namespace
- Matches releases by **both** release name prefix AND model short names
  (e.g., `llmdbench-infra`, `qwen-qwen3-32b-ms`, `qwen-qwen3-32b-gaie`)
- Uninstalls matched releases via `helm uninstall`
- Deletes HTTPRoutes in the namespace
- Deletes completed/failed Jobs
- Skipped for standalone deployments

### Step 02: Clean Harness (Global)

Cleans up harness/benchmark resources:

- Deletes benchmark-related ConfigMaps (preprocesses, harness configs)
- Deletes harness pods (data access pods, benchmark runners)
- Deletes the context secret used for harness communication
- Operates in the harness namespace

### Step 03: Delete Resources (Global)

Deletes namespaced resources in both deploy and harness namespaces:

**Normal mode:** Targets specific resource types with known patterns:

- Deployments, StatefulSets, ReplicaSets, DaemonSets
- Services, Endpoints, EndpointSlices
- Pods, Jobs, CronJobs
- ConfigMaps, Secrets (excluding system ones and HF token)
- PersistentVolumeClaims
- ServiceAccounts (excluding `default`)
- Roles, RoleBindings
- NetworkPolicies, PodDisruptionBudgets, HorizontalPodAutoscalers
- InferenceModels, InferencePools (CRDs)

**Deep mode:** Discovers all available API resource types and deletes everything:

- Queries `kubectl api-resources --namespaced=true`
- Iterates over each resource type
- Deletes all resources found (non-fatal failures for protected resources)

### Step 04: Clean Cluster Roles (Global, Admin + Modelservice only)

Removes cluster-scoped ClusterRoles and ClusterRoleBindings:

- Matches by release name label and model short names
- Only runs when `--non-admin` is NOT set and method is modelservice
- Skipped for standalone deployments

### Step 05: Management Cleanup (Global)

Post-teardown management and summary operations.

## Helm Release Matching

Teardown step 01 uses a two-pronged matching strategy:

1. **Release name match:** Checks if the release name (e.g., `llmdbench`) appears in the Helm release name
2. **Model label match:** Checks if any model short name (e.g., `qwen-qwen3-32b`) appears in the Helm release name

This catches all three release patterns created by helmfile:

- `infra-llmdbench` (infrastructure)
- `{model.shortName}-ms` (modelservice)
- `{model.shortName}-gaie` (GAIE inference extension)

## Usage

```bash
# Normal teardown
llmdbenchmark --spec config/specification/guides/inference-scheduling.yaml.j2 teardown

# Deep clean
llmdbenchmark --spec ... teardown --deep

# Specific steps only
llmdbenchmark --spec ... teardown -s 0-2

# Specify method explicitly
llmdbenchmark --spec ... teardown -t modelservice

# Override namespace
llmdbenchmark --spec ... teardown -p my-namespace

# Override both namespaces (deploy,harness)
llmdbenchmark --spec ... teardown -p deploy-ns,harness-ns
```
