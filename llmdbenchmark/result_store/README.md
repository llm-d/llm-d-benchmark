# Results Store 🚀

The Results Store is a powerful, Git-like system designed to help you manage, track, and share LLM benchmark results efficiently. It ensures your data remains consistent and structured, making it easy to analyze or visualize in tools like Prism.

Whether you are running local experiments or managing a large-scale evaluation pipeline, Results Store handles the complexity of organizing your results.

---

## 📖 Core Concepts

Before diving into use cases, here are a few concepts to understand:
*   **Store Root**: A repository folder initialized with a `.result_store/` directory. It tracks your configuration and staged runs.
*   **Workspace**: A local directory containing benchmark results, plans, and reports.
*   **Staging Area**: A holding area where you verify your results before sharing them with others.
*   **Strict Taxonomy**: Results are stored remotely in a predictable structure (`scenario/model/hardware/timestamp`), ensuring fast searching and consistent analysis.

---

## 🛠️ Use Cases

All operations are performed via the `llmdbenchmark results` command.

### 1. Setting Up Your Environment

To start using the Results Store, you need to initialize it and configure where your data will be stored remotely.

#### Use Case: Initialize a new store
Run this in the directory where you want to manage your benchmark results.
```bash
llmdbenchmark results init
```

#### Use Case: Configure remote storage endpoints
Manage connections to shared remote buckets (e.g., prod, staging).
*   **List configured remotes**:
    ```bash
    llmdbenchmark results remote ls
    ```
*   **Add a new remote**:
    ```bash
    llmdbenchmark results remote add prod gs://my-team-results/published
    ```
*   **Remove a remote**:
    ```bash
    llmdbenchmark results remote rm prod
    ```

---

### 2. Publishing Benchmark Results

After running benchmarks, you want to share your results with the team by uploading them to a remote bucket.

#### Use Case: Check status of local runs
See which runs are new (untracked), modified, or ready to be pushed (staged).
```bash
llmdbenchmark results status
```

#### Use Case: Stage results for publishing
Select specific runs to prepare for upload.
```bash
llmdbenchmark results add workspaces/my-test-run
```
> [!NOTE]
> **Interactive Guardrails**: If your run is missing critical metadata (like hardware counts), the tool will prompt you to fill it in interactively!

#### Use Case: Push staged results to remote
Upload all staged runs to the specified remote.
```bash
# Pushes to 'staging' remote by default
llmdbenchmark results push staging
```

---

### 3. Exploring and Retrieving Results

You want to look at results shared by others or pull them down to inspect them locally.

#### Use Case: List runs in a remote bucket
Browse what is available in the remote storage.
```bash
llmdbenchmark results ls prod
```
> [!TIP]
> You can use wildcards (like `*`) to filter by model or hardware!
> Example: `llmdbenchmark results ls prod -m "llama-*"`

#### Use Case: Retrieve a run and recreate workspace
Download a specific run and reconstruct the local workspace directory structure.
```bash
llmdbenchmark results pull prod --run-uid c6bc210e
```
*Your workspace will be reconstructed, for example at `./workspaces/prod_default_c6bc210e/` ready for review!*

---

### 4. Advanced: Batch Operations & Wildcards

Save time by operating on multiple runs at once using wildcards.

#### Use Case: Stage multiple runs by pattern
```bash
# Stage all runs starting with 'c6bc'
llmdbenchmark results add "c6bc*"
```

#### Use Case: Pull multiple runs by pattern
```bash
# Pull all runs matching the pattern from remote
llmdbenchmark results pull prod --run-uid "c6bc*"
```

---

### 5. Advanced: Ad-hoc Operations (Store-less)

You can perform quick transfers without setting up a full repository environment.

#### Use Case: Quick upload of a directory
Directly push any directory without executing `add` first, by providing the path. It will fallback to default remote URLs if no store is initialized.
```bash
llmdbenchmark results push staging workspaces/my-test-run
```

#### Use Case: Quick download without anchoring a store
Pull a run to the current directory (it will be placed in `./workspaces/`) without having run `results init`.
```bash
llmdbenchmark results pull prod --run-uid "a1b2c3d4"
```
