# llmdbenchmark.cleanup

Cleanup phase of the benchmark lifecycle. Removes what a benchmark run leaves behind in the namespace: leftover harness launcher pods, the benchmark ConfigMaps, the data-access pod, the harness service, and the workload PVC.

## Step Ordering

Steps are registered in `steps/__init__.py` via `get_cleanup_steps()` and execute in order:

| Step | Name | Scope | Description |
|------|------|-------|-------------|
| 00 | `CleanupResourcesStep` | global | Delete benchmark leftovers for every rendered stack |

## Step Details

### Step 00 -- Cleanup Resources

For each rendered stack, reads the resource names from its `config.yaml` (`namespace.name`, `harness.namespace`, `harness.podLabel`, `harness.name`, `labels.app`, `storage.workloadPvc.name`) and deletes:

- harness launcher pods and the `<harness.name>-profiles`, `llm-d-benchmark-*` and `llmdbench-harness-scripts` ConfigMaps in the harness namespace
- the data-access pod, the `labels.app` service and the workload PVC in the workload namespace

The data-access pod is deleted before the PVC so the claim's protection finalizer can clear. `--keep-pvc` preserves the PVC. Stacks that resolve to identical names are cleaned once. The step is idempotent: every delete uses `--ignore-not-found`, so a namespace with nothing in it is a success.
