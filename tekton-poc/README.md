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
7. Upload the results to external storage (not yet implemented)\
8. Delete the experiment namespace

A PipelineRun is created that embeds a Pipeline containing one Task with a matrix of values for a set of factors.  An example is `pipelinerun-matrix.yaml`.

## Use

1. Create a namespace, for example: $NAMESPACE and set to current context:
    ```shell
    kubectl create ns $NAMESPACE
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
    kubectl apply -f pipeline/roles.yaml
    ```
4. Deploy the steps and tasks:
    ```shell
    kubectl apply -f pipeline/stepactions.yaml
    kubectl apply -f pipeline/experiment-task.yaml
    ```
5. Run experiments (set the parameter `namespace` to $NAMESPACE):
    ```shell
    kubectl apply -f pipeline/pipelinerun-matrix.yaml
    ```

See the TaskRun objects created:

```shell
tkn tr list
```

See the logs for a TaskRun:

```shell
tkn tr logs <taskrun_name> -f
```

## Managing Parallelism

The sample `PipelineRun` (`pipeline/pipelinerun-matrix.yaml`) executes all the tasks in parallel. Depdending on the size of the matrix, this may require a large number of resources.
A _matrix_ based `Task` can be unrolled into multiple tasks to reduce the parallelism.
The utility script `utility/transform-pr-parallel.py` does this as follows:

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
