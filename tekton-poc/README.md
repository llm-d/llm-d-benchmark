# Benchmarking with Tekton

This folder contains a proof of concept 

## Tekton Basics
A **Pipeline** is set of **Tasks**. Tasks run in parallel. The execution flow can be controlled implicitly (via one task consume a result of another) or explcitly with mechanisms like `runAfter`, `when` and `finally`.
A **Task** is a sequence of **Steps**. Steps run sequentially. The step can programmatically determine to execute or skip.

To execute a **Pipeline** create a **PipelineRun**, 
an object that identifies:
 - the Pipeline to execute and 
 - the values of any parameters

Tekton creates a **TaskRun** for each Task in the Pipeline.
A TaskRun is an object that identifies: 
 - the Task and 
 - the values of any parameters (passed from the PipelineRun)

The TaskRun is implemented by a Pod
Each Step is implemented by a Container in the Pod.

## Supported Benchmarking Use Cases

Given a matrix of factors and values, measure performance of a model over a matrix of factors/values
Factors may be model deployment related, such as: model, endpoint picker configuration, parallelism, ...
Factors may also be workload related, for example: question_len, output_len,workload_profile, ...

This proof of concept currently implements a variation of the inference-scheduling [scenairo](https://github.com/llm-d/llm-d-benchmark/blob/main/scenarios/guides/inference-scheduling.sh)/[experiment](https://github.com/llm-d/llm-d-benchmark/blob/main/experiments/inference-scheduling.yaml).

## Approach

A single Task measures performance over a single set of values from the factor/values matrix. This task implements steps:

1. Create/prepare an experiment namespace
2. Deploy a Gateway
3. Configure GAIE
4. Download the model from HuggingFace to a PVC
5. Deploy the model
6. Run the workload for a single set of parameters
7. Upload the results to external storage (s3)
8. Delete the experiment namespace (not yet implemented)

A PipelineRun is created that embeds a Pipeline containing one Task with a matrix of values for a set of factors.  An example is `pipelinerun-matrix.yaml`.

## Usage

### Setup

1. Create a namespace where the Tekton pipeline will execute.
    ```shell
    export $NAMESPACE=your_namespace
    kubectl create ns $NAMESPACE
    ```
    For convenience, set the current context:
    ```shell
    kubectl config set-context --current --namespace $NAMESPACE
    ```

2. Deploy a secret `hf-secret` containing your HuggingFace token in the namespace.
    ```shell
    kubectl create secret generic hf-secret \
          --namespace ${NAMESPACE} \
          --from-literal="HF_TOKEN=${HF_TOKEN}" \
          --dry-run=client -o yaml | kubectl apply -f -
    ```

3. Give the task needed permissions
    ```shell
    envsubst '$NAMESPACE' < pipeline/roles.yaml | kubectl apply -f -
    ```

4. Create a RWX PVC `workspace-pvc` for storing execution results. This PVC is shared between all tasks.  For example:
    ```shell
    cat <<EOF | kubectl apply -f -
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata:
        name: workspace-pvc
        namespace: ${NAMESPACE}
    spec:
        accessModes:
        - ReadWriteMany
        resources:
            requests:
                storage: 20Gi
        storageClassName: ocs-storagecluster-cephfs
        volumeMode: Filesystem
    EOF
    ```

### Starting a `Pipeline`

1. Deploy the steps and tasks:
    ```shell
    kubectl apply -f pipeline/stepactions.yaml
    kubectl apply -f pipeline/experiment-task.yaml
    ```

2. Run experiments (set the parameter `namespace` to $NAMESPACE):
    ```shell
    kubectl apply -f pipeline/pipelinerun-matrix.yaml
    ```

### Inspection

See the `PipelineRun` object created:

```shell
tkn pr list
```

See the `TaskRun` objects created:

```shell
tkn tr list
```

See the logs for a `TaskRun`:

```shell
tkn tr logs <taskrun_name> -f
```

Describe a `TaskRun`:

```shell
tkn tr describe <taskrun_name>
```

### Cleanup

Delete the `PipelineRun`: 

```shell
tkn pr delete <pipelinerun_name> -f
```

**Note**: The current implementation does not remove the namespaces created by each sweep step. Manually delete them to release all their resources.  If you leave them, subsequent executions of the pipeline will attempt to reuse the resources.

## Managing Parallelism

The default PipelineSpec (in `pipeline/pipelinerun-matrix.yaml`) executes all the tasks in parallel. It can be modified in a number of ways to reduce the amount of parallel execution (at the expense of time).

Some examples are provided:

- `pipeline/pipelinerun-matrix-subset.yaml`: Uses `matrix.include` to list an explicit set of combinations to execute.
- `pipeline/pipelinerun-sequential-1.yaml`: Executes 1 task at a time. Each task depends on the previous one.
- `pipeline/pipelinerun-sequential-4-barrier.yaml`: Executes 4 tasks at a time. When all 4 complete, the next 4 start.
- `pipeline/pipelinerun-sequential-4-sliding.yaml`: Executes 4 tasks at a time. When one task completes another starts.
- `pipeline/pipelinerun-sequential-unroll-gaiePluginConfig.yaml`: Creates one task for each value of one dimention of the matrix. Each is executed in sequence. However, for other dimensions, parallel execution takes place.

The utility script `utility/transform-pr-parallel.py` can be used to transform a default `PipelineRun` into alternatives as follows:

1. Unroll a single parameter into one `Task` per value. Each resulting Task defines a matrix over the remaining parameters.

    ```shell
    python transform-pr.py pipelinerun-matrix.yaml --unroll gaiePluginConfig -o pr-unrolled.yaml
    ```

2. Unroll multiple parameters into [their Cartesian product] Tasks. Each resulting Task defines a matrix over the remaining parameters.

    ```shell
    python transform-pr.py pipelinerun-matrix.yaml --unroll gaiePluginConfig,question_len -o pr-unrolled-2.yaml
    ```

3. Unroll all the parameters into [their Cartian product] Tasks. Allow _n_ to run at once. This can be done using a _barrier_ strategy or a _sliding_window_ strategy

    ```shell
    # Barrier (default)
    python transform-pr.py pipelinerun-matrix.yaml -n 3 -o pr-expanded-barrier.yaml

    # Sliding window
    python transform-pr.py pipelinerun-matrix.yaml -n 3 --sliding-window -o pr-expanded-sliding.yaml
    ```

## Cautions

- be sure to set the namespace parameter in the pipeline run; this is where the pipeline runs and is the base of the name for each experiment
- the upload of data is not yet implemented
- there are hardcoded assumptions/values about the use case in several places; these will be removed as more use cases are explored
