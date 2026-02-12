---
name: convert-guide
description: Convert llm-d guides to benchmark scenario and experiment files. Use when the user wants to convert an llm-d deployment guide (Helm values or kustomize files) into llm-d-benchmark scenario files. Triggers on /convert-guide command with a URL or local path to a guide.
---

# Convert llm-d Guide to Benchmark Files

## Purpose

Convert llm-d deployment guides (Helm values files) into llm-d-benchmark scenario files (and optionally experiment files for testing). The converter:
- Extracts configuration from Helm values YAML or kustomize patches
- Maps values to `LLMDBENCH_*` environment variables
- Generates scenario files (shell scripts with exports) - **always created**
- Generates experiment files (YAML for parameter sweeps) - **only when testing specs provided**

## Usage

```
/convert-guide <url-or-path>
/convert-guide <url-or-path> with <harness> <profile>
/convert-guide <url-or-path> varying <parameter> from <start> to <end>
```

### Defaults

- **Default Harness**: `inference-perf`
- **Default Profile**: `sanity_random.yaml`

See [references/harnesses.md](references/harnesses.md) for all available harnesses and profiles.

### Examples

```bash
# Convert a guide
/convert-guide https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling

# Specify custom harness and profile
/convert-guide https://github.com/llm-d/llm-d/tree/main/guides/pd-disaggregation with inference-perf shared_prefix_synthetic

# Convert with accelerator variant
/convert-guide https://github.com/llm-d/llm-d/tree/main/guides/pd-disaggregation xpu

# Add parameter variations (creates scenario + experiment files)
/convert-guide ~/guides/config.yaml varying decode_replicas from 1 to 4
```

## Workflow

### Step 1: Parse the Input

Extract from the user's command:
- **Source**: URL or local file path
- **Harness**: Load generator (default: `inference-perf`)
- **Profile**: Workload profile (default: `sanity_random.yaml`)
- **Testing Specifications**: Check for variations, treatments, or test parameters

**IMPORTANT**: If NO testing specifications are provided, create ONLY a scenario file. Do NOT create an experiment file unless the user explicitly specifies what to test or vary.

### Step 2: Read Reference Files

Read these reference files to understand the mapping rules and defaults:

1. **Mapping Rules**: `references/mappings.md` (bundled with this skill)
   - Contains Helm path to LLMDBENCH variable mappings
   - Notes on transformations and special handling

2. **Default Values**: `setup/env.sh` (codebase file - read at runtime)
   - Current default values for all LLMDBENCH variables
   - Only override values that differ from defaults

**Note:** Do NOT read files from `scenarios/guides/` or `experiments/` as examples - these are generated outputs.

### Step 3: Fetch Guide Contents

**Detect the Configuration Approach:**
1. Look for `kustomization.yaml` files in the guide directory
2. If found, this is a **kustomize-based guide**
3. If only `values.yaml` files are found, this is a **Helm values-based guide**

**TRACK SOURCE FILES**: Record file paths AND line numbers for inclusion in scenario file documentation.

**For Helm Values-Based Guides:**
- Fetch `ms-*/values.yaml` (ModelService) and `gaie-*/values.yaml` (GAIE InferenceExtension)
- For GitHub URLs, first fetch directory listing to identify subdirectories

**For Kustomize-Based Guides:**
- Read main `kustomization.yaml` and follow `resources:` references to find base files
- Extract configuration from patches (`op: replace`, `op: add`)
- Document the full kustomize hierarchy
- See [references/templates.md](references/templates.md) for kustomize patch mappings

### Step 4: Extract Configuration

Parse the YAML and extract key configuration sections. See [references/mappings.md](references/mappings.md) for complete field mappings.

**From ModelService (`ms-*/values.yaml`):**
- Model configuration (`modelArtifacts.*`)
- Container images (if explicitly specified)
- Decode/Prefill stage settings (replicas, parallelism, args, env, volumes)
- vLLM launch arguments

**From GAIE (`gaie-*/values.yaml`):**
- InferenceExtension settings (image, flags, plugins)
- **CRITICAL**: If `pluginsCustomConfig` exists, extract the ENTIRE YAML content
- InferencePool settings (ports, labels)

### Step 5: Map to LLMDBENCH Variables

Using [references/mappings.md](references/mappings.md):
1. Find the corresponding `LLMDBENCH_*` variable for each extracted value
2. Compare against defaults from `setup/env.sh`
3. Only include variables that differ from defaults
4. Note any unmappable values in comments

### Step 6: Apply llm-d-benchmark Standard Practices

**CRITICAL**: Always apply these practices regardless of what the guide specifies:

1. **Always use custom model command**:
   ```bash
   export LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom
   ```

2. **Always include preprocess in EXTRA_ARGS**:
   ```bash
   export LLMDBENCH_VLLM_COMMON_PREPROCESS="python3 /setup/preprocess/set_llmdbench_environment.py; source \$HOME/llmdbench_env.sh"
   export LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS=$LLMDBENCH_VLLM_COMMON_PREPROCESS

   export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
   cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
   REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \
   vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
   --host 0.0.0.0 \
   --served-model-name REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
   --port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
   <...additional vllm flags from guide...>
   EOF
   ```

3. **Always include preprocesses volume mount**:
   ```bash
   - name: preprocesses
     mountPath: /setup/preprocess
   ```

4. **Always include preprocesses configMap volume** (FIRST in list):
   ```bash
   - name: preprocesses
     configMap:
       defaultMode: 0755
       name: llm-d-benchmark-preprocesses
   ```

See [references/patterns.md](references/patterns.md) for complete patterns including volumes, environment variables, GAIE plugins, and accelerator-specific configurations.

### Step 7: Generate Output Files

**Scenario File**: Always created. Use template from [references/templates.md](references/templates.md).

Every environment variable MUST include source documentation:
```bash
# =============================================================================
# SOURCE: <relative-path-to-source-file>
# Lines <line-numbers>:
#   <original YAML/JSON content from source>
# =============================================================================
export LLMDBENCH_<VARIABLE_NAME>=<value>
```

**Experiment File**: Only created if user specified testing requirements. Use template from [references/templates.md](references/templates.md).

### Step 8: Validate Standard Practices and Documentation

Before displaying or writing files, verify:

**Standard Practices Checklist:**
- `LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND=custom` is set
- `LLMDBENCH_VLLM_COMMON_PREPROCESS` is defined
- `LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS` is set
- `DECODE_EXTRA_ARGS` starts with `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS; \`
- `DECODE_EXTRA_ARGS` contains `vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \`
- Volume mounts include `preprocesses` mount at `/setup/preprocess`
- Volumes include `preprocesses` configMap (should be first in list)
- If guide has `inferenceExtension.pluginsCustomConfig`, then `LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS` is defined
- If guide specifies explicit container image version, then `LLMDBENCH_LLMD_IMAGE_TAG` is set

**Source Documentation Checklist:**
- Every environment variable has a `# SOURCE:` comment block
- SOURCE blocks include file path and line numbers
- SOURCE blocks include original content
- Framework additions marked as "Benchmark framework convention (not in guide)"

### Step 9: Write Files and Report

**CRITICAL**: You MUST call the Write tool. Do NOT skip to success message without writing.

**File Paths (use `ai.` prefix to indicate Claude-generated):**
- Scenario files: `scenarios/guides/ai.<guide_name>.sh`
- Experiment files: `experiments/ai.<guide_name>.yaml`

**ONLY after Write tool succeeds**, report a summary including:
- File path(s) written
- Source configuration files used
- Key configuration highlights (model, replicas, notable vLLM flags, etc.)
- Usage instructions

## Error Handling

1. **Invalid URL**: Report error, suggest checking the URL
2. **Missing File**: Report clearly if local path doesn't exist
3. **Invalid YAML**: Show error and problematic section
4. **Unknown Helm Values**: Log unmapped values as comments
5. **Missing Required Values**: Warn if critical values (like model name) are missing

## Deriving Guide Name

Extract from source:
1. **From URL**: Last path segment before `values.yaml`
2. **From Local Path**: Filename without extension, or parent directory name
3. **Sanitization**: Replace spaces/special chars with hyphens, lowercase

## Reference Files

- **Mapping Rules**: [references/mappings.md](references/mappings.md)
- **Templates**: [references/templates.md](references/templates.md)
- **Complex Patterns**: [references/patterns.md](references/patterns.md)
- **Harnesses/Profiles**: [references/harnesses.md](references/harnesses.md)
- **Default Values**: `setup/env.sh` (codebase file, read at runtime)
