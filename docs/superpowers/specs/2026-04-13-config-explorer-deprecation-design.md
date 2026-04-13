# config-explorer Deprecation and llm-d-planner Migration

**Date:** 2026-04-13
**Branch:** planner

## Overview

Deprecate and delete the `config_explorer/` package from this repository. Migrate all active consumers to import `capacity_planner` from [llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner) instead. Move the analysis helper modules (`explorer.py`, `constants.py`, `plotting.py`) to `docs/analysis/` where they are kept as reference until Prism is ready.

## Background

`config_explorer` served two purposes:
1. **Capacity planning** (`capacity_planner.py`) — used at benchmark startup by `capacity_validator.py` to validate GPU/model fit before a run.
2. **Analysis helpers** (`explorer.py`, `plotting.py`, `constants.py`, `benchmark_report/`) — used by `docs/analysis/analysis.ipynb` for post-run data analysis.

The capacity planner has been upstreamed to `llm-d-planner` (`src/planner/capacity_planner.py`) as an identical file. The analysis helpers have already been copied to `docs/analysis/` on this branch. `config_explorer` is now redundant.

## Goals

- Remove `config_explorer/` from the repository entirely.
- Point all consumers of `config_explorer.capacity_planner` to `planner.capacity_planner` (llm-d-planner).
- Pin the llm-d-planner install to a specific commit for reproducibility.
- Keep `docs/analysis/` (`explorer.py`, `constants.py`, `plotting.py`, `benchmark_report` symlink, `analysis.ipynb`) intact as reference — to be deleted when Prism is ready.
- Update all user-facing documentation to reflect the new dependency.

## Non-Goals

- Integrating llm-d-planner's broader planning capabilities (intent extraction, Streamlit UI, etc.) into this repo.
- Modifying `analysis.ipynb` — it stays as-is with bare imports designed to run from `docs/analysis/`.
- Replacing `docs/analysis/` with a proper installable package.

## Changes

### 1. Python code

**`llmdbenchmark/utilities/capacity_validator.py`**

Change the import block from:
```python
from config_explorer.capacity_planner import (
    KVCacheDetail,
    allocatable_kv_cache_memory,
    ...
)
```
to:
```python
from planner.capacity_planner import (
    KVCacheDetail,
    allocatable_kv_cache_memory,
    ...
)
```
All 14 imported names are identical — the file is the same in both packages.

### 2. Install script

**`install.sh`**

Replace:
```bash
pip install -e "${config_explorer_dir}"
```
with:
```bash
pip install "git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"
```

Remove the `config_explorer` directory-existence check, the `config_explorer is already installed` idempotency guard, and the post-install importability checks that reference `config_explorer`.

Update the installer's printed step list (currently lists "Installs config_explorer") to say "Installs planner (llm-d-planner)".

### 3. CI workflows

**`.github/workflows/ci-nightly-benchmark-cks.yaml`**
**`.github/workflows/ci-nighly-benchmark-ocp.yaml`**

Replace:
```yaml
run: pip install ./config_explorer
```
with:
```yaml
run: pip install "git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4"
```

### 4. Documentation

**`docs/upstream-versions.md`**
- Remove the "Python Dependencies (config_explorer)" section (lines covering huggingface_hub, transformers, pydantic, pandas, numpy, scipy, matplotlib, PyYAML, llm-optimizer pinned to config_explorer's pyproject.toml).
- Add a "Python Dependencies (planner)" section pointing to llm-d-planner's pyproject.toml for the same packages where relevant.

**`util/setup_precommit.sh`**
- Update comments that reference `config_explorer` or its install (lines 13, 39) to reference `planner` (llm-d-planner).

**`README.md`**
- Remove `pip install -e config_explorer/` from the manual install steps.
- Update the step description from "Installs `llmdbenchmark` and `config_explorer`" to "Installs `llmdbenchmark` and `planner` (llm-d-planner)".
- Remove the "Configuration Explorer" section.

**`docs/quickstart.md`**
- Update the installer description to remove the reference to `config_explorer`; replace with `planner` (llm-d-planner).

**`CONTRIBUTING.md`**
- Remove any reference to `config_explorer`.

**`llmdbenchmark/utilities/README.md`**
- Update the library reference from `config_explorer.capacity_planner` to `planner.capacity_planner` (llm-d-planner).

**`docs/tutorials/kubecon/README.md`**
- Remove the Config Explorer section entirely (the `pip install ./config_explorer`, `config-explorer start`, and `streamlit run ./config_explorer/Capacity_Planner.py` steps).

### 5. Delete config_explorer/

Delete the entire `config_explorer/` directory.

### 6. docs/analysis/ (no changes needed)

`docs/analysis/` already contains `constants.py`, `explorer.py`, `plotting.py`, and `benchmark_report -> ../../benchmark_report/` on this branch. The notebook uses bare imports and is designed to be launched with `jupyter lab` from `docs/analysis/`. No changes are needed here.

## Dependency Bloat Risk

`llm-d-planner`'s `pyproject.toml` declares many dependencies beyond what `config_explorer` required: `fastapi`, `uvicorn`, `ollama`, `psycopg2-binary`, `jinja2`, `requests`, and `llm-optimizer` (a git dependency). Only `planner.capacity_planner` is used by this repo; the web framework, database client, and LLM runtime are unused.

This will increase install size and CI image weight. The known trade-off is accepted because `capacity_planner.py` will eventually be extracted as a standalone minimal package in llm-d-planner, at which point the install can be slimmed down. For now, the full package is installed.

## Pinned Commit

llm-d-planner commit: `f51812bebca30e0291ec541bd2ef2acf0572e8a4`

This should be updated when llm-d-planner cuts a release or when a newer commit is validated.

## Future Work

- **When llm-d-planner is stable**: unpin to a release tag or semver range.
- **When capacity_planner is extracted to a minimal package in llm-d-planner**: update the install to use only that package, reducing dependency bloat.
- **When Prism is ready**: delete `docs/analysis/` (`explorer.py`, `constants.py`, `plotting.py`, `benchmark_report` symlink).
