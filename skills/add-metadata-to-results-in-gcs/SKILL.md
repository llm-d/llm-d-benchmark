---
name: add-metadata-to-results-in-gcs
description: Add stack metadata to existing benchmark results in Google Cloud Storage (GCS). Use when the user wants to backfill or enrich previous reports with stack metadata from a specific serving cluster endpoint.
---

# Add Stack Metadata to Benchmark Results in GCS

## Purpose

Inject stack metadata (discovered from a live Kubernetes service or provided explicitly) into previously generated benchmark report files in Google Cloud Storage. This skill performs surgical text-replacement to append metadata to the YAML files without causing floating-point precision loss or indentation churn.

## Usage

```
/add-metadata <gcs-bucket-path> with endpoint <url>
```

### Defaults

- **Default File Pattern**: `benchmark_report*.yaml` / `benchmark_report*.json.yaml`

### Examples

```bash
# Add stack metadata to a specific benchmark run folder in GCS
/add-metadata gs://my-bucket/benchmark_results with endpoint http://35.212.211.79
```

## Workflow

### Step 1: Parse the Input

Extract from the user's command:
- **GCS Source**: Path to the benchmark results folder in GCS (e.g., `gs://my-bucket/benchmark_results`)
- **Endpoint**: URL of the ModelService or GAIE endpoint serving the benchmarked model (e.g., `http://35.212.211.79`)

### Step 2: Verify Cluster Context

Ensure that the current Kubernetes context (`kubectl config current-context`) corresponds to the serving cluster for the specified endpoint. Ask the user for confirmation before proceeding.

### Step 3: Download & Backup

1. Download the benchmark folder from GCS to a local temporary directory.
2. Create a backup copy of the downloaded folder to allow for safe diff generation and rollback if needed.

### Step 4: Stack Discovery

Run discovery against the provided endpoint to extract stack metadata:
```bash
source .venv/bin/activate
python -m llm_d_stack_discovery.cli <endpoint-url> -f benchmark-report -o /tmp/stack_metadata.json
```

### Step 5: Surgical Injection

Create and execute a non-destructive Python script to append the stack metadata block to the end of the benchmark report YAML files. The script should:
1. Read the generated JSON metadata.
2. Convert it into standard YAML lines matching the benchmark report format (under a `stack:` block).
3. Locate each target benchmark report file (`benchmark_report*_metrics.json.yaml`).
4. Append the standard YAML lines natively to prevent ruamel/yaml from stripping comments, trailing spaces, or altering float representations of unrelated metrics.

### Step 6: Review & Confirm

1. Generate a diff between the backup folder and the modified folder.
2. Present the diff to the user to prove that only the `stack:` section was appended and no other metrics were modified.
3. Ask for user confirmation before uploading back to GCS.

### Step 7: Push to GCS

Upload the modified files back to their original GCS location:
```bash
gcloud storage cp -r /tmp/modified_folder/* <gcs-bucket-path>/
```

## Validation Plan

- **Local Diff**: Verify with a local diff patch that only the `stack` metadata was added and metrics were untouched.
- **Log Files**: Ensure non-report files (e.g., `config.yaml`) are excluded from injection.
