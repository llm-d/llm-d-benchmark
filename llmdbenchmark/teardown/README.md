# llmdbenchmark.teardown

Teardown phase of the benchmark lifecycle. Removes resources deployed by a previous standup, including Helm releases, namespaced resources, and cluster-scoped roles.

## Step Ordering

Steps are registered in `steps/__init__.py` via `get_teardown_steps()` and execute in order:

| Step | Name | Scope | Description |
|------|------|-------|-------------|
| 00 | `TeardownPreflightStep` | global | Load plan config and print summary banner showing what will be torn down |
| 01 | `UninstallHelmStep` | global | Uninstall Helm releases, OpenShift routes, and model download jobs |
| 02 | `CleanHarnessStep` | global | Remove harness resources (ConfigMaps, pods, secrets) from the harness namespace |
| 03 | `DeleteResourcesStep` | global | Delete namespaced resources in normal mode or deep mode |
| 04 | `CleanClusterRolesStep` | global | Remove cluster-scoped ClusterRoles and ClusterRoleBindings created during standup |

## Step Details

### Step 00 -- Preflight

Loads the rendered plan config (`config.yaml`) from the first rendered stack. Populates `context.namespace`, `context.deployed_methods`, and other fields needed by subsequent steps. Prints a phase banner showing the cluster, namespace, model, and methods to be torn down. Fails if no rendered plan config is found.

### Step 01 -- Uninstall Helm Releases

Skipped when `modelservice` is not in `context.deployed_methods`.

For each target namespace:
- Uninstalls Helm releases matching the release prefix and model labels.
- Deletes OpenShift routes (if on OpenShift) matching the release prefix.
- Deletes model download jobs.

### Step 02 -- Clean Harness

Removes harness resources from the harness namespace:
- Profile ConfigMaps (matching `workload-` prefix or harness-related names).
- Load generator pods (labelled `app=llmdbench-harness-launcher,function=load_generator`).
- Context secrets (the secret name is read from `control.contextSecretName` in the plan config).

### Step 03 -- Delete Resources

Two modes:

**Normal mode** (default): Deletes resources matching known patterns across both model and harness namespaces. Patterns differ based on deploy method:
- Standalone patterns: `standalone`, `download-model`, `testinference`, `lmbenchmark`
- Modelservice patterns: `llm-d-benchmark-preprocesses`, `p2p`, `inference-gateway`, `inferencepool`, `httproute`, `llm-route`, `base-model`, `endpoint-picker`, etc.

System resources (e.g. `kube-root-ca.crt`, `odh-trusted-ca-bundle`) are excluded.

Resource types checked: `deployment`, `httproute`, `service`, `gateway`, `gatewayparameters`, `inferencepool`, `inferencemodel`, `configmap`, `ingress`, `pod`, `job`.

**Deep mode** (`--deep`): Deletes ALL resources in both namespaces across a broad set of resource types: `deployment`, `service`, `secret`, `gateway`, `inferencemodel`, `inferencepool`, `httproute`, `configmap`, `job`, `role`, `rolebinding`, `serviceaccount`, `hpa`, `va`, `servicemonitor`, `podmonitor`, `pod`, `pvc`. On OpenShift, also deletes `route` resources.

### Step 04 -- Clean Cluster Roles

Skipped when `context.non_admin` is True or when `modelservice` is not in `context.deployed_methods`.

Deletes ClusterRoles and ClusterRoleBindings matching the release prefix. Also deletes well-known modelservice ClusterRoles by suffix: `modelservice-endpoint-picker`, `modelservice-epp-metrics-scrape`, `modelservice-manager`, `modelservice-metrics-auth`, `modelservice-admin`, `modelservice-editor`, `modelservice-viewer`.

## Dry-Run Behavior

In dry-run mode, all kubectl and helm commands are logged without execution. Each step reports what it would delete. `CommandResult` objects are returned with `dry_run=True` and `exit_code=0`.

## Files

```
teardown/
├── __init__.py              -- Package marker
└── steps/
    ├── __init__.py           -- Step registry (get_teardown_steps)
    ├── step_00_preflight.py
    ├── step_01_uninstall_helm.py
    ├── step_02_clean_harness.py
    ├── step_03_delete_resources.py
    └── step_04_clean_cluster_roles.py
```
