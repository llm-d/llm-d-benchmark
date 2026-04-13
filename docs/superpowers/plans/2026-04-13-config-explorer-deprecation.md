# config-explorer Deprecation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete `config_explorer/` from this repo and migrate all consumers to `llm-d-planner`, pinned at commit `f51812bebca30e0291ec541bd2ef2acf0572e8a4`.

**Architecture:** The capacity planner is an identical file in both packages — only the import path changes (`config_explorer.capacity_planner` → `planner.capacity_planner`). The planner package is installed via a pinned git URL. Analysis helpers (`explorer.py`, `constants.py`, `plotting.py`) are already in `docs/analysis/` and need no changes.

**Tech Stack:** Python, pip, bash, GitHub Actions YAML, Markdown

---

## Chunk 1: Code and install changes

### Task 1: Write a smoke test for the planner import

There are no existing tests for `capacity_validator.py`. Write one that imports all 14 functions from `planner.capacity_planner` and calls a basic one so we know the import and package work.

**Files:**
- Create: `llmdbenchmark/utilities/tests/test_capacity_validator_import.py`

- [ ] **Step 1: Create the test file**

```python
# llmdbenchmark/utilities/tests/test_capacity_validator_import.py
"""Smoke test: verify planner.capacity_planner is importable and functional."""

import pytest


def test_planner_capacity_planner_imports():
    """All 14 functions used by capacity_validator must be importable from planner."""
    from planner.capacity_planner import (
        KVCacheDetail,
        allocatable_kv_cache_memory,
        available_gpu_memory,
        estimate_vllm_activation_memory,
        estimate_vllm_cuda_graph_memory,
        estimate_vllm_non_torch_memory,
        find_possible_tp,
        get_model_config_from_hf,
        get_model_info_from_hf,
        get_text_config,
        gpus_required,
        max_concurrent_requests,
        max_context_len,
        model_memory_req,
        model_total_params,
    )
    assert callable(gpus_required)
    assert callable(find_possible_tp)
    assert callable(available_gpu_memory)


def test_gpus_required_basic():
    """gpus_required(tp, pp, dp) should return tp*pp*dp."""
    from planner.capacity_planner import gpus_required
    assert gpus_required(tp=2, pp=1, dp=1) == 2
    assert gpus_required(tp=4, pp=2, dp=1) == 8
    assert gpus_required(tp=1, pp=1, dp=1) == 1


def test_available_gpu_memory_basic():
    """available_gpu_memory should return gpu_memory * utilization."""
    from planner.capacity_planner import available_gpu_memory
    result = available_gpu_memory(80, 0.9)
    assert abs(result - 72.0) < 0.01
```

- [ ] **Step 2: Run test — expect FAIL (planner not installed yet)**

```bash
python -m pytest llmdbenchmark/utilities/tests/test_capacity_validator_import.py -v
```

Expected: `ModuleNotFoundError: No module named 'planner'`

---

### Task 2: Install planner from pinned commit

- [ ] **Step 1: Install planner**

```bash
pip install "git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"
```

- [ ] **Step 2: Verify planner installs and tests pass**

```bash
python -m pytest llmdbenchmark/utilities/tests/test_capacity_validator_import.py -v
```

Expected: All 3 tests PASS.

---

### Task 3: Swap import in capacity_validator.py

**Files:**
- Modify: `llmdbenchmark/utilities/capacity_validator.py:11-27`

- [ ] **Step 1: Change the import (line 11)**

In `llmdbenchmark/utilities/capacity_validator.py`, change:
```python
from config_explorer.capacity_planner import (
```
to:
```python
from planner.capacity_planner import (
```

The 14 imported names (`KVCacheDetail`, `allocatable_kv_cache_memory`, `available_gpu_memory`, `estimate_vllm_activation_memory`, `estimate_vllm_cuda_graph_memory`, `estimate_vllm_non_torch_memory`, `find_possible_tp`, `get_model_config_from_hf`, `get_model_info_from_hf`, `get_text_config`, `gpus_required`, `max_concurrent_requests`, `max_context_len`, `model_memory_req`, `model_total_params`) are unchanged.

- [ ] **Step 2: Run smoke test**

```bash
python -m pytest llmdbenchmark/utilities/tests/test_capacity_validator_import.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 3: Verify capacity_validator itself imports cleanly**

```bash
python -c "from llmdbenchmark.utilities.capacity_validator import run_capacity_planner; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add llmdbenchmark/utilities/tests/test_capacity_validator_import.py \
        llmdbenchmark/utilities/capacity_validator.py
git commit -m "feat: migrate capacity_validator to planner.capacity_planner (llm-d-planner)"
```

---

### Task 4: Update install.sh

**Files:**
- Modify: `install.sh`

Four places to change, all in the Python packages section (lines ~100–527):

- [ ] **Step 1: Update the DESCRIPTION block (around line 104)**

Change:
```
5. Installs config_explorer           (editable: pip install -e config_explorer/)
```
to:
```
5. Installs planner (llm-d-planner)   (pip install git+https://...@<commit>)
```

- [ ] **Step 2: Replace the config_explorer install block (lines ~472–492)**

Remove this entire block:
```bash
# ---------------------------------------------------------------------------
# 2. Install config_explorer (editable)
# ---------------------------------------------------------------------------
config_explorer_dir="${SCRIPT_DIR}/config_explorer"

if [[ ! -d "$config_explorer_dir" ]]; then
    echo "ERROR: config_explorer directory not found at ${config_explorer_dir}"
    exit 1
fi

if grep -q "config_explorer is already installed." "$dependencies_checked_file" 2>/dev/null; then
    print_pkg config_explorer ""
else
    if ${PIP_CMD} install -e "${config_explorer_dir}" --quiet 2>/dev/null; then
        print_pkg config_explorer "(installed)"
        echo "config_explorer is already installed." >> "$dependencies_checked_file"
    else
        echo "ERROR: Failed to install config_explorer!"
        exit 1
    fi
fi
```

Replace with:
```bash
# ---------------------------------------------------------------------------
# 2. Install planner (llm-d-planner)
# ---------------------------------------------------------------------------
PLANNER_GIT="git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"

if grep -q "planner is already installed." "$dependencies_checked_file" 2>/dev/null; then
    print_pkg planner ""
else
    if ${PIP_CMD} install "${PLANNER_GIT}" --quiet 2>/dev/null; then
        print_pkg planner "(installed)"
        echo "planner is already installed." >> "$dependencies_checked_file"
    else
        echo "ERROR: Failed to install planner (llm-d-planner)!"
        exit 1
    fi
fi
```

- [ ] **Step 3: Update the import verification block (lines ~517–524)**

Remove:
```bash
if ! ${PYTHON_CMD} -c "import config_explorer" 2>/dev/null; then
    echo "WARNING: config_explorer installed but not importable"
    import_ok=false
fi
if ! ${PYTHON_CMD} -c "from config_explorer.capacity_planner import model_memory_req" 2>/dev/null; then
    echo "WARNING: config_explorer.capacity_planner not importable"
    import_ok=false
fi
```

Replace with:
```bash
if ! ${PYTHON_CMD} -c "import planner" 2>/dev/null; then
    echo "WARNING: planner installed but not importable"
    import_ok=false
fi
if ! ${PYTHON_CMD} -c "from planner.capacity_planner import model_memory_req" 2>/dev/null; then
    echo "WARNING: planner.capacity_planner not importable"
    import_ok=false
fi
```

- [ ] **Step 4: Verify install.sh is valid bash**

```bash
bash -n install.sh
```

Expected: no output (no syntax errors).

- [ ] **Step 5: Commit**

```bash
git add install.sh
git commit -m "feat: install planner (llm-d-planner) instead of config_explorer in install.sh"
```

---

### Task 5: Update CI workflows

**Files:**
- Modify: `.github/workflows/ci-nightly-benchmark-cks.yaml:91-93`
- Modify: `.github/workflows/ci-nighly-benchmark-ocp.yaml:95-97`

- [ ] **Step 1: Update ci-nightly-benchmark-cks.yaml**

Change (lines 91-92):
```yaml
      - name: Install config explorer dependencies
        run: pip install ./config_explorer
```
to:
```yaml
      - name: Install planner dependencies
        run: pip install "git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"
```

- [ ] **Step 2: Update ci-nighly-benchmark-ocp.yaml**

Change (lines 95-96):
```yaml
      - name: Install config explorer dependencies
        run: pip install ./config_explorer
```
to:
```yaml
      - name: Install planner dependencies
        run: pip install "git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"
```

- [ ] **Step 3: Validate YAML syntax**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci-nightly-benchmark-cks.yaml')); print('cks OK')"
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci-nighly-benchmark-ocp.yaml')); print('ocp OK')"
```

Expected: `cks OK` and `ocp OK`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci-nightly-benchmark-cks.yaml \
        .github/workflows/ci-nighly-benchmark-ocp.yaml
git commit -m "feat: install planner (llm-d-planner) in CI instead of config_explorer"
```

---

## Chunk 2: Documentation updates

### Task 6: Update README.md

**Files:**
- Modify: `README.md`

Two places to update:

- [ ] **Step 1: Update the installer description (line 209)**

Change:
```
5. Installs `llmdbenchmark` and `config_explorer` in editable mode
```
to:
```
5. Installs `llmdbenchmark` and `planner` ([llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner)) in editable mode
```

- [ ] **Step 2: Update the Manual Install section (line 219)**

Change:
```bash
pip install -e config_explorer/
```
to:
```bash
pip install "git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"
```

- [ ] **Step 3: Remove the Configuration Explorer section (lines ~566–568)**

Remove these three lines:
```markdown
### [Configuration Explorer](config_explorer/README.md)

The configuration explorer is a library that helps find the most cost-effective, optimal configuration for serving models on llm-d based on hardware specification, workload characteristics, and SLO requirements. A "Capacity Planner" is provided as an initial component to help determine if a vLLM configuration is feasible for deployment.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: update README.md to reference planner instead of config_explorer"
```

---

### Task 7: Update docs/quickstart.md

**Files:**
- Modify: `docs/quickstart.md`

- [ ] **Step 1: Find and update the config_explorer mention (line ~116)**

Change:
```
Clone the repository and run the installer. It creates `.venv/`, installs the `llmdbenchmark` and `config_explorer` Python packages, and provisions every system tool the framework calls out to.
```
to:
```
Clone the repository and run the installer. It creates `.venv/`, installs the `llmdbenchmark` and `planner` ([llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner)) Python packages, and provisions every system tool the framework calls out to.
```

- [ ] **Step 2: Commit**

```bash
git add docs/quickstart.md
git commit -m "docs: update quickstart.md to reference planner instead of config_explorer"
```

---

### Task 8: Update CONTRIBUTING.md, llmdbenchmark/utilities/README.md, util/setup_precommit.sh

**Files:**
- Modify: `CONTRIBUTING.md`
- Modify: `llmdbenchmark/utilities/README.md`
- Modify: `util/setup_precommit.sh`

- [ ] **Step 1: Update CONTRIBUTING.md (line 69)**

The long line on line 69 includes a reference to `config_explorer`. Replace any mention of `config_explorer` in that line with `planner (llm-d-planner)`.

Run to find the exact text:
```bash
grep -n "config_explorer\|config-explorer" CONTRIBUTING.md
```

Then make the targeted edit.

- [ ] **Step 2: Update llmdbenchmark/utilities/README.md (line 55)**

Change:
```
Validates vLLM deployment parameters against model and GPU hardware constraints using the `config_explorer.capacity_planner` library.
```
to:
```
Validates vLLM deployment parameters against model and GPU hardware constraints using the `planner.capacity_planner` library ([llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner)).
```

- [ ] **Step 3: Update util/setup_precommit.sh (lines 13 and 39)**

Run to see current text:
```bash
grep -n "config_explorer" util/setup_precommit.sh
```

Update both comment lines replacing `config_explorer` with `planner (llm-d-planner)`.

- [ ] **Step 4: Commit**

```bash
git add CONTRIBUTING.md llmdbenchmark/utilities/README.md util/setup_precommit.sh
git commit -m "docs: update CONTRIBUTING, utilities README, setup_precommit to reference planner"
```

---

### Task 9: Update docs/upstream-versions.md

**Files:**
- Modify: `docs/upstream-versions.md`

- [ ] **Step 1: Replace the Python Dependencies (config_explorer) section (lines 45–57)**

Remove:
```markdown
## Python Dependencies (config_explorer)

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **huggingface_hub** | `>=0.34.4` | minimum version | `config_explorer/pyproject.toml` | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) |
| **transformers** | `>=4.55.4` | minimum version | `config_explorer/pyproject.toml` | [huggingface/transformers](https://github.com/huggingface/transformers) |
| **pydantic** | `>=2.11.7` | minimum version | `config_explorer/pyproject.toml` | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| **pandas** | `>=2.3.1` | minimum version | `config_explorer/pyproject.toml` | [pandas-dev/pandas](https://github.com/pandas-dev/pandas) |
| **numpy** | `>=2.3.2` | minimum version | `config_explorer/pyproject.toml` | [numpy/numpy](https://github.com/numpy/numpy) |
| **scipy** | `>=1.16.1` | minimum version | `config_explorer/pyproject.toml` | [scipy/scipy](https://github.com/scipy/scipy) |
| **matplotlib** | `>=3.10.5` | minimum version | `config_explorer/pyproject.toml` | [matplotlib/matplotlib](https://github.com/matplotlib/matplotlib) |
| **PyYAML** | `>=6.0.2` | minimum version | `config_explorer/pyproject.toml` | [yaml/pyyaml](https://github.com/yaml/pyyaml) |
| **llm-optimizer** | `main` | git branch | `config_explorer/pyproject.toml` | [bentoml/llm-optimizer](https://github.com/bentoml/llm-optimizer) |
```

Replace with:
```markdown
## Python Dependencies (planner / llm-d-planner)

Installed via: `pip install git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4`

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **huggingface_hub** | `>=0.34.4` | minimum version | `llm-d-planner/pyproject.toml` | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) |
| **transformers** | `>=4.55.4` | minimum version | `llm-d-planner/pyproject.toml` | [huggingface/transformers](https://github.com/huggingface/transformers) |
| **pydantic** | `==2.12.5` | exact version | `llm-d-planner/pyproject.toml` | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| **pandas** | `==3.0.2` | exact version | `llm-d-planner/pyproject.toml` | [pandas-dev/pandas](https://github.com/pandas-dev/pandas) |
| **fastapi** | `>=0.115.3` | minimum version | `llm-d-planner/pyproject.toml` | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| **uvicorn** | `==0.44.0` | exact version | `llm-d-planner/pyproject.toml` | [encode/uvicorn](https://github.com/encode/uvicorn) |
| **ollama** | `==0.6.1` | exact version | `llm-d-planner/pyproject.toml` | [ollama/ollama-python](https://github.com/ollama/ollama-python) |
| **psycopg2-binary** | `==2.9.11` | exact version | `llm-d-planner/pyproject.toml` | [psycopg/psycopg2](https://github.com/psycopg/psycopg2) |
| **llm-optimizer** | git main | git ref | `llm-d-planner/pyproject.toml` | [bentoml/llm-optimizer](https://github.com/bentoml/llm-optimizer) |
```

- [ ] **Step 2: Commit**

```bash
git add docs/upstream-versions.md
git commit -m "docs: update upstream-versions.md to reference planner instead of config_explorer"
```

---

### Task 10: Update docs/tutorials/kubecon/README.md

**Files:**
- Modify: `docs/tutorials/kubecon/README.md`

- [ ] **Step 1: Find the Config Explorer section**

```bash
grep -n "config_explorer\|Config Explorer\|config-explorer" docs/tutorials/kubecon/README.md
```

- [ ] **Step 2: Remove the section**

Lines 307–314 contain:
```markdown
Feel free to use the [Config Explorer](../../../config_explorer/) to explore the data.

```
pip install ./config_explorer
streamlit run ./config_explorer/Capacity_Planner.py
```

The UI should be up and running. You can get a preview of the Config Explorer [here](https://drive.google.com/file/d/1lzdj2P65yhQG3w5gsVxULkTqSYwMH3ec/view?usp=sharing).
```

Delete this entire block (from the "Feel free to use..." line through the preview link line). Keep any surrounding context clean — do not leave dangling section headers.

- [ ] **Step 3: Commit**

```bash
git add docs/tutorials/kubecon/README.md
git commit -m "docs: remove config_explorer references from KubeCon tutorial"
```

---

## Chunk 3: Delete config_explorer/ and final verification

### Task 11: Verify nothing outside config_explorer/ still imports from it

- [ ] **Step 1: Grep for remaining references**

```bash
grep -r "config_explorer\|config-explorer" \
  --include="*.py" --include="*.yaml" --include="*.sh" --include="*.md" \
  --exclude-dir=".venv" --exclude-dir="config_explorer" \
  --exclude-dir=".git" --exclude-dir="hypotheses-copy" \
  --exclude-dir="docs/superpowers" \
  .
```

Expected: zero results (or only results inside `docs/plans/` / `docs/specs/` which are historical records, and `docs/proposals/` which is a proposal doc — those are acceptable).

If any unexpected hits appear, fix them before proceeding.

---

### Task 12: Delete config_explorer/

- [ ] **Step 1: Delete the directory**

```bash
git rm -r config_explorer/
```

- [ ] **Step 2: Verify it's gone**

```bash
ls config_explorer/ 2>&1
```

Expected: `ls: config_explorer/: No such file or directory`

- [ ] **Step 3: Verify all tests still pass**

```bash
python -m pytest llmdbenchmark/utilities/tests/test_capacity_validator_import.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 4: Verify capacity_validator imports cleanly**

```bash
python -c "from llmdbenchmark.utilities.capacity_validator import run_capacity_planner; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat!: delete config_explorer/ — capacity planner now in llm-d-planner"
```

---

### Task 13: Final check — no stale config_explorer references in active code

- [ ] **Step 1: Final grep sweep**

```bash
grep -r "config_explorer\|config-explorer" \
  --include="*.py" --include="*.yaml" --include="*.sh" \
  --exclude-dir=".venv" --exclude-dir=".git" \
  .
```

Expected: zero results in `.py`, `.yaml`, `.sh` files.

- [ ] **Step 2: Run the full test suite**

```bash
python -m pytest llmdbenchmark/ -v --tb=short 2>&1 | tail -20
```

Expected: test suite passes (or same failures as before this change — do not introduce new failures).

- [ ] **Step 3: Verify install.sh dry-run**

```bash
bash -n install.sh && echo "install.sh syntax OK"
```

Expected: `install.sh syntax OK`

- [ ] **Step 4: Confirm docs/analysis/ is unaffected**

```bash
ls docs/analysis/
```

Expected: `analysis.ipynb  benchmark_report  constants.py  explorer.py  plotting.py  README.md  aggregate_runs.py  to_be_incorporated`
