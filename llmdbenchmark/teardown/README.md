# llmdbenchmark.teardown

Teardown phase of the benchmark lifecycle. Removes resources deployed by a previous standup, including Helm releases, namespaced resources, and cluster-scoped roles.

## Step Ordering

Steps are registered in `steps/__init__.py` via `get_teardown_steps()` and execute in order:

| Step | Name | Description |
|------|------|-------------|
| 00 | `TeardownPreflightStep` | Load plan config and print summary banner showing what will be torn down |
| 01 | `UninstallHelmStep` | Uninstall Helm releases, OpenShift routes, and model download jobs |
| 02 | `CleanHarnessStep` | Remove harness resources (ConfigMaps, pods, secrets) from the harness namespace |
| 03 | `DeleteResourcesStep` | Delete namespaced resources in normal mode or deep mode (`--deep` wipes all resources in both namespaces) |
| 04 | `CleanClusterRolesStep` | Remove cluster-scoped ClusterRoles and ClusterRoleBindings created during standup |

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
