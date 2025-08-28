# Bash to Python Conversion Guide

This document outlines the conversion process from bash scripts to Python for the llm-d-benchmark project.

## Overview

As part of ongoing efforts to modernize the codebase and improve maintainability, we are converting bash scripts in `setup/steps/` to Python. This conversion provides several benefits:

- **Better Error Handling**: Python's exception handling is more robust than bash
- **Improved Readability**: Python code is generally easier to read and maintain
- **Enhanced Testing**: Python has better unit testing frameworks
- **Cross-platform Compatibility**: Python scripts are more portable across different operating systems
- **API Integration**: Direct use of Kubernetes Python client instead of subprocess calls

## Conversion Pattern

### 1. Environment Variable Handling

**Bash Pattern:**
```bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh
# Direct access to variables like $LLMDBENCH_HF_TOKEN
```

**Python Pattern:**
```python
# Parse environment variables into ev dictionary
ev = {}
for key, value in os.environ.items():
    if "LLMDBENCH_" in key:
        ev[key.split("LLMDBENCH_")[1].lower()] = value

# Access via: ev["infra_dir"]
```

### 2. Function Imports

**Bash Pattern:**
```bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh
# Functions available globally
```

**Python Pattern:**
```python
from functions import announce, llmdbench_execute_cmd
```

### 3. Command Execution

**Prefer Native Python Libraries:**
```python
# Use native Python libraries instead of shell commands
import git

# Instead of: llmdbench_execute_cmd("git clone repo")
repo = git.Repo.clone_from(url=git_repo, to_path=local_path, branch=git_branch)
```

**Shell Commands (when necessary):**
```python
# Only use shell commands when no suitable Python library exists
llmdbench_execute_cmd(
    actual_cmd="kubectl apply -f manifest.yaml", 
    dry_run=dry_run, 
    verbose=verbose
)
```

### 4. Recommended Python Libraries

Use native Python libraries instead of shell commands whenever possible:

- **Git operations**: `GitPython` instead of `git` commands
- **Kubernetes operations**: `pykube` instead of `kubectl` commands  
- **HTTP requests**: `requests` instead of `curl` commands
- **File operations**: `pathlib.Path` instead of `mkdir`/`cp` commands
- **JSON/YAML**: `json`/`yaml` libraries instead of `jq`/`yq` commands

**Benefits:**
- Better error handling and type safety
- More testable and mockable code
- Platform independence
- Better integration with Python ecosystem

### 5. File Structure

**Bash Pattern:**
```bash
if [[ ! -d llm-d-infra ]]; then
    # clone
else
    # update
fi
```

**Python Pattern:**
```python
from pathlib import Path

llm_d_infra_path = Path(infra_dir) / "llm-d-infra"
if not llm_d_infra_path.exists():
    # clone
else:
    # update
```

## Implementation Selection

The framework supports both bash and Python implementations for each step. You can control which implementation to use via environment variables:

```bash
# Use Python implementation for step 00
export LLMDBENCH_CONTROL_STEP_00_IMPLEMENTATION=py

# Use bash implementation for step 00 (default)
export LLMDBENCH_CONTROL_STEP_00_IMPLEMENTATION=sh
```

## Conversion Checklist

When converting a bash script to Python:

1. **Create the Python file** with the same base name (e.g., `00_ensure_llm-d-infra.py`)
2. **Follow the established patterns** from existing conversions (e.g., `04_ensure_model_namespace_prepared.py`)
3. **Handle environment variables** using the `ev` dictionary pattern
4. **Import required functions** from `functions.py`
5. **Maintain identical functionality** - the Python version should produce the same results
6. **Create unit tests** for both bash and Python versions
7. **Validate equivalence** using dry-run mode
8. **Update documentation** as needed

## Testing

### Unit Tests

Create unit tests in `util/unit_test/`:
- `test_<step_name>.sh` - Tests for bash version
- `test_<step_name>.py` - Tests for Python version
- `validate_<step_name>_conversion.sh` - Validation script comparing both versions

### Validation

Use dry-run mode to compare outputs:
```bash
# Test bash version
LLMDBENCH_HF_TOKEN=token LLMDBENCH_CONTROL_STEP_00_IMPLEMENTATION=sh LLMDBENCH_CONTROL_DRY_RUN=1 ./setup/standup.sh -s 00

# Test Python version  
LLMDBENCH_HF_TOKEN=token LLMDBENCH_CONTROL_STEP_00_IMPLEMENTATION=py LLMDBENCH_CONTROL_DRY_RUN=1 ./setup/standup.sh -s 00
```

## Completed Conversions

- `04_ensure_model_namespace_prepared.sh` → `04_ensure_model_namespace_prepared.py`
- `00_ensure_llm-d-infra.sh` → `00_ensure_llm-d-infra.py`

## Next Steps

Based on complexity analysis, recommended conversion order:

1. `03_ensure_user_workload_monitoring_configuration.sh` (20 lines)
2. `07_deploy_gaie.sh` (47 lines)
3. `01_ensure_local_conda.sh` (73 lines)
4. `02_ensure_gateway_provider.sh` (83 lines)
5. `09_smoketest.sh` (83 lines)
6. `05_ensure_harness_namespace_prepared.sh` (208 lines)
7. `06_deploy_vllm_standalone_models.sh` (214 lines)
8. `08_deploy_via_modelservice.sh` (274 lines)

Note: Step 10 environment variable exists but no corresponding script has been implemented yet.

## Contributing

When contributing a conversion:

1. Create a feature branch: `convert-step-XX-to-python`
2. Follow the established patterns
3. Include comprehensive tests
4. Validate functionality equivalence
5. Update this documentation
6. Submit a pull request with clear description of changes