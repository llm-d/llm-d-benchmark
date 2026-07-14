# Running eval-containers benchmarks on OpenShift

This guide explains how to run eval-containers benchmarks (aider-polyglot, GAIA) on an
OpenShift cluster. It covers the one-time cluster prerequisites, how to write a
`cluster-config` file for your specific cluster, and the run command.

## How it works

The benchmark framework uses a three-layer merge for configuration:

```
defaults.yaml  →  scenario.yaml  →  --cluster-config FILE
(in repo)          (in repo)         (your local file, not committed)
```

The scenario file (`config/scenarios/examples/eval-containers-aider-polyglot.yaml`) is
generic and works on any Kubernetes cluster. Cluster-specific values — storage class,
service account, root privilege — live only in your `cluster-config` file.

## Step 1: One-time cluster prerequisites

OpenShift restricts pods from running as root by default (Security Context Constraints).
The eval container images require root (UID 0). A cluster-admin must grant the necessary
permissions once per namespace.

Run these commands as a user with **cluster-admin** privileges:

```bash
NAMESPACE=<your-namespace>

# Create a ServiceAccount that is allowed to run as root
oc create serviceaccount anyuid-sa -n $NAMESPACE

# Grant it the anyuid SCC (allows runAsUser: 0)
oc adm policy add-scc-to-user anyuid -z anyuid-sa -n $NAMESPACE
```

> **Why `anyuid-sa`?**
> OpenShift's `anyuid` SCC allows a pod to specify any UID, including root (0).
> By binding it to a ServiceAccount, only pods that explicitly request that SA
> can run as root — the rest of the namespace remains locked down.
> The name `anyuid-sa` is a convention; you can use any name as long as you
> reference the same name in your cluster-config file.

## Step 2: Create your cluster-config file

Create a YAML file on your local machine (do not commit it to the repo). It only needs
to contain the values that differ from the defaults for your specific cluster.

### IBM Cloud OpenShift (VPC)

```yaml
# ~/my-clusters/exgentic-openshift.yaml
storage:
  workloadPvc:
    accessModes:
      - ReadWriteMany
    storageClassName: ibmc-vpc-file-1000-iops

harness:
  serviceAccount: anyuid-sa
  runAsUser: 0

dataAccess:
  serviceAccount: anyuid-sa
  runAsUser: 0
```

### Generic OpenShift (ODF/Ceph storage)

```yaml
storage:
  workloadPvc:
    accessModes:
      - ReadWriteMany
    storageClassName: ocs-storagecluster-cephfs  # or your cluster's RWX class

harness:
  serviceAccount: anyuid-sa
  runAsUser: 0

dataAccess:
  serviceAccount: anyuid-sa
  runAsUser: 0
```

### Common OpenShift storage classes

| Platform | Storage class | Access mode |
|---|---|---|
| IBM Cloud VPC (exgentic) | `ibmc-vpc-file-1000-iops` | ReadWriteMany |
| OpenShift Data Foundation | `ocs-storagecluster-cephfs` | ReadWriteMany |
| Azure Red Hat OpenShift | `azurefile-csi` | ReadWriteMany |
| ROSA (AWS) | `efs-sc` | ReadWriteMany |

Ready-to-use example files are in `config/cluster-configs/examples/`.

## Step 3: Run the benchmark

```bash
llmdbenchmark --spec examples/eval-containers-aider-polyglot run \
  --cluster-config ~/my-clusters/exgentic-openshift.yaml \
  --base-dir . \
  -U "https://<your-litellm-endpoint>" \
  -m "claude-sonnet-4-6" \
  -p <namespace> \
  -g OPENAI_API_KEY \
  -j 5 \
  --analyze \
  --wait-timeout 3600
```

## What the framework expects vs. what you must provision

| Requirement | Who does it | How |
|---|---|---|
| `anyuid-sa` ServiceAccount exists | Cluster-admin (you, once) | `oc create serviceaccount anyuid-sa` |
| `anyuid` SCC bound to `anyuid-sa` | Cluster-admin (you, once) | `oc adm policy add-scc-to-user anyuid -z anyuid-sa` |
| RWX PVC storage class exists | Cluster infra | Verify with `oc get sc` |
| API key secret in namespace | You | `kubectl create secret generic eval-secrets --from-literal=OPENAI_API_KEY=...` |
| The framework creates | Framework | Namespace (if missing), PVC, ConfigMaps, harness pods |

## Troubleshooting

**Pod fails with `unable to validate against any security context constraint`**
→ The pod's ServiceAccount does not have the `anyuid` SCC. Re-run the `oc adm policy` command from Step 1.

**PVC stuck in `Pending`**
→ The `storageClassName` in your cluster-config does not exist. Run `oc get sc` to list available storage classes.

**`access-to-harness-data-workload-pvc` pod fails to start**
→ Check that `dataAccess.serviceAccount` in your cluster-config matches the SA you created in Step 1.
