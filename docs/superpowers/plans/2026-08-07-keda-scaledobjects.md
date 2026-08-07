# Generic KEDA ScaledObject Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic `keda.scaledObjects` scenario key that renders multiple KEDA ScaledObjects with configurable Prometheus auth (`none` or `bearer-secret`), working on any Kubernetes cluster.

**Architecture:** A new `keda.py` standup module + two new Jinja templates handle the generic path entirely. Step 03 calls `_install_keda_if_enabled` unconditionally (outside the OpenShift gate). Step 09 applies the rendered ScaledObjects template per stack. The existing `eppKedaSaturation` path is untouched.

**Tech Stack:** Python 3.13, Jinja2, PyYAML, KEDA v2 CRD API (`keda.sh/v1alpha1`), pytest

## Global Constraints

- Do not modify `keda_saturation.py`, `keda_prometheus_auth.py`, `wva.py`, or any of their templates.
- `keda-prometheus-auth` is the TriggerAuthentication name for this new path; `prometheus-auth` belongs to the old path — never collide.
- `keda.scaledObjects` and `eppKedaSaturation` must coexist in the same namespace without interference.
- The new standup path must NOT gate on `context.is_openshift`.
- All new code follows the existing stub-based test pattern (see `tests/test_teardown_wva.py`).
- Secret for `bearer-secret` authMode must be in the deploy namespace (KEDA `secretTargetRef` does not support cross-namespace).

---

## File Map

**New files:**
- `llmdbenchmark/standup/keda.py` — standup helpers: `stacks_enabling_keda`, `install_keda_for_namespace`
- `config/templates/jinja/31_keda-scaledobjects.yaml.j2` — generic ScaledObject loop template
- `config/templates/jinja/32_keda-triggerauthentication.yaml.j2` — TriggerAuthentication for bearer-secret mode
- `tests/test_standup_keda.py` — unit tests for `keda.py`

**Modified files:**
- `llmdbenchmark/standup/steps/step_03_workload_monitoring.py` — add `_install_keda_if_enabled`, call unconditionally from `execute`
- `llmdbenchmark/standup/steps/step_09_deploy_modelservice.py` — add `_apply_keda_stack_resources`, call after `_apply_epp_keda_stack_resources`

---

## Task 1: New module `keda.py` with stack-discovery helpers

**Files:**
- Create: `llmdbenchmark/standup/keda.py`
- Create: `tests/test_standup_keda.py`

**Interfaces:**
- Produces:
  - `stacks_enabling_keda(rendered_stacks: list[Path]) -> list[tuple[Path, dict]]`
  - `install_keda_for_namespace(cmd, context, stack_path: Path, namespace: str, errors: list) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_standup_keda.py
"""Tests for llmdbenchmark/standup/keda.py stack-discovery and install helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
import pytest

from llmdbenchmark.standup.keda import stacks_enabling_keda, install_keda_for_namespace


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _StubLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")

    def log_error(self, msg: str, **_: Any) -> None:
        self.messages.append(f"ERR: {msg}")


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    kube_calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.kube_calls.append(args)
        return _StubResult(success=True)


@dataclass
class _StubContext:
    logger: _StubLogger = field(default_factory=_StubLogger)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_stack(tmp_path: Path, name: str, *, cfg: dict) -> Path:
    stack_dir = tmp_path / name
    stack_dir.mkdir(parents=True)
    (stack_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    return stack_dir


def _write_ta_template(stack_dir: Path, namespace: str, secret_name: str) -> None:
    """Write a rendered TriggerAuthentication YAML (template 32)."""
    content = f"""apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: keda-prometheus-auth
  namespace: {namespace}
spec:
  secretTargetRef:
  - parameter: bearerToken
    name: {secret_name}
    key: bearerToken
  - parameter: ca
    name: {secret_name}
    key: ca.crt
"""
    (stack_dir / "32_keda-triggerauthentication.yaml").write_text(content)


def _write_so_template(stack_dir: Path) -> None:
    """Write a minimal rendered ScaledObjects YAML (template 31)."""
    content = """apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: test-so
  namespace: test-ns
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: test-deploy
  minReplicaCount: 1
  maxReplicaCount: 5
  triggers: []
"""
    (stack_dir / "31_keda-scaledobjects.yaml").write_text(content)


# ---------------------------------------------------------------------------
# Tests: stacks_enabling_keda
# ---------------------------------------------------------------------------

class TestStacksEnablingKeda:
    def test_returns_stack_with_scaled_objects(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {"scaledObjects": [{"name": "so1"}]},
        })
        result = stacks_enabling_keda([stack])
        assert len(result) == 1
        assert result[0][0] == stack

    def test_empty_scaled_objects_excluded(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {"scaledObjects": []},
        })
        assert stacks_enabling_keda([stack]) == []

    def test_missing_keda_key_excluded(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", cfg={"namespace": {"name": "ns1"}})
        assert stacks_enabling_keda([stack]) == []

    def test_missing_config_yaml_excluded(self, tmp_path: Path) -> None:
        stack_dir = tmp_path / "empty"
        stack_dir.mkdir()
        assert stacks_enabling_keda([stack_dir]) == []

    def test_multiple_stacks_only_enabled_returned(self, tmp_path: Path) -> None:
        s1 = _write_stack(tmp_path, "s1", cfg={"keda": {"scaledObjects": [{"name": "x"}]}})
        s2 = _write_stack(tmp_path, "s2", cfg={"namespace": {"name": "ns2"}})
        result = stacks_enabling_keda([s1, s2])
        assert len(result) == 1
        assert result[0][0] == s1


# ---------------------------------------------------------------------------
# Tests: install_keda_for_namespace
# ---------------------------------------------------------------------------

class TestInstallKedaForNamespace:
    def test_none_auth_applies_scaledobjects_only(self, tmp_path: Path) -> None:
        """authMode=none: applies ScaledObjects template, no TriggerAuthentication."""
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {"prometheus": {"authMode": "none"}, "scaledObjects": [{"name": "x"}]},
        })
        _write_so_template(stack)
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert errors == []
        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        # Only the ScaledObjects template applied — no TA
        assert len(applied) == 1
        assert "31_keda-scaledobjects" in applied[0][2]

    def test_bearer_secret_auth_applies_ta_then_scaledobjects(self, tmp_path: Path) -> None:
        """authMode=bearer-secret: applies TA first, then ScaledObjects."""
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {
                "prometheus": {"authMode": "bearer-secret", "secretName": "my-secret"},
                "scaledObjects": [{"name": "x"}],
            },
        })
        _write_ta_template(stack, "test-ns", "my-secret")
        _write_so_template(stack)
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert errors == []
        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        assert len(applied) == 2
        paths_applied = [args[2] for args in applied]
        assert any("32_keda-triggerauthentication" in p for p in paths_applied)
        assert any("31_keda-scaledobjects" in p for p in paths_applied)
        # TA must be applied before ScaledObjects
        ta_idx = next(i for i, p in enumerate(paths_applied) if "32_keda" in p)
        so_idx = next(i for i, p in enumerate(paths_applied) if "31_keda" in p)
        assert ta_idx < so_idx

    def test_bearer_secret_missing_ta_template_warns(self, tmp_path: Path) -> None:
        """Missing TA template logs a warning but does not append an error."""
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {
                "prometheus": {"authMode": "bearer-secret", "secretName": "my-secret"},
                "scaledObjects": [{"name": "x"}],
            },
        })
        _write_so_template(stack)  # no TA template
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert any("WARN" in m for m in ctx.logger.messages)

    def test_missing_so_template_is_noop(self, tmp_path: Path) -> None:
        """Missing ScaledObjects template: nothing applied, no errors."""
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {"prometheus": {"authMode": "none"}, "scaledObjects": [{"name": "x"}]},
        })
        # No template file written
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert errors == []
        assert cmd.kube_calls == []

    def test_kube_apply_failure_appends_error(self, tmp_path: Path) -> None:
        """A kubectl apply failure appends to errors list."""
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {"prometheus": {"authMode": "none"}, "scaledObjects": [{"name": "x"}]},
        })
        _write_so_template(stack)

        @dataclass
        class _FailCmd:
            kube_calls: list = field(default_factory=list)

            def kube(self, *args: str, **_: Any) -> _StubResult:
                self.kube_calls.append(args)
                return _StubResult(success=False, stderr="permission denied")

        cmd = _FailCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert len(errors) == 1
        assert "permission denied" in errors[0]
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_standup_keda.py -v
```
Expected: `ImportError` or `ModuleNotFoundError` — `keda.py` doesn't exist yet.

- [ ] **Step 3: Implement `llmdbenchmark/standup/keda.py`**

```python
"""Standup helpers for generic KEDA ScaledObject support.

Handles stacks with `keda.scaledObjects` defined. Supports authMode `none`
(no TriggerAuthentication) and `bearer-secret` (user-supplied Secret).
Does not gate on OpenShift — works on any Kubernetes cluster.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext


def stacks_enabling_keda(
    rendered_stacks: list[Path],
) -> list[tuple[Path, dict]]:
    """Return (stack_path, config) pairs for stacks with keda.scaledObjects defined and non-empty."""
    pairs: list[tuple[Path, dict]] = []
    for stack_path in rendered_stacks:
        cfg_file = stack_path / "config.yaml"
        if not cfg_file.exists():
            continue
        try:
            with open(cfg_file, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        if cfg.get("keda", {}).get("scaledObjects"):
            pairs.append((stack_path, cfg))
    return pairs


def install_keda_for_namespace(
    cmd: CommandExecutor,
    context: ExecutionContext,
    stack_path: Path,
    namespace: str,
    errors: list,
) -> None:
    """Apply TriggerAuthentication (bearer-secret only) then the ScaledObjects template.

    For authMode=none, only the ScaledObjects template is applied.
    For authMode=bearer-secret, the TriggerAuthentication (template 32) is applied
    first so KEDA can resolve auth before the ScaledObject triggers fire.
    """
    cfg_file = stack_path / "config.yaml"
    try:
        with open(cfg_file, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return

    auth_mode = cfg.get("keda", {}).get("prometheus", {}).get("authMode", "none")

    if auth_mode == "bearer-secret":
        ta_yaml = _find_yaml(stack_path, "32_keda-triggerauthentication")
        if ta_yaml and _has_yaml_content(ta_yaml):
            result = cmd.kube("apply", "-f", str(ta_yaml), "-n", namespace, check=False)
            if not result.success:
                errors.append(
                    f"Failed to apply keda TriggerAuthentication in ns/{namespace}: "
                    f"{result.stderr}"
                )
                return
        else:
            context.logger.log_warning(
                f"keda TriggerAuthentication template (32_keda-triggerauthentication) "
                f"not found for ns/{namespace}. KEDA bearer-secret auth will not work."
            )

    so_yaml = _find_yaml(stack_path, "31_keda-scaledobjects")
    if not so_yaml or not _has_yaml_content(so_yaml):
        return

    result = cmd.kube("apply", "-f", str(so_yaml), "-n", namespace, check=False)
    if not result.success:
        errors.append(
            f"Failed to apply keda ScaledObjects in ns/{namespace}: {result.stderr}"
        )


def _find_yaml(stack_path: Path, stem_prefix: str) -> Path | None:
    for candidate in stack_path.glob(f"{stem_prefix}*.yaml"):
        return candidate
    return None


def _has_yaml_content(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_standup_keda.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add llmdbenchmark/standup/keda.py tests/test_standup_keda.py
git commit -m "feat: add keda.py standup module with stack-discovery and install helpers"
```

---

## Task 2: Jinja templates for ScaledObjects and TriggerAuthentication

**Files:**
- Create: `config/templates/jinja/31_keda-scaledobjects.yaml.j2`
- Create: `config/templates/jinja/32_keda-triggerauthentication.yaml.j2`

**Interfaces:**
- Consumes: Jinja context variables `keda`, `namespace`, `model`, `model_id_label`
- Produces: rendered YAML files under each stack's output directory (applied by Task 4)

No unit tests for Jinja templates directly — they are exercised by the render pipeline integration tests in Task 3.

- [ ] **Step 1: Write `31_keda-scaledobjects.yaml.j2`**

```jinja
{# ============================================================================
   31_keda-scaledobjects.yaml.j2

   Generic KEDA ScaledObject renderer. One YAML document per entry in
   keda.scaledObjects, separated by ---. All prometheus triggers share the
   serverAddress from keda.prometheus. Auth is injected based on authMode:
     none         -- no authenticationRef, no authModes
     bearer-secret -- authModes: bearer + authenticationRef: keda-prometheus-auth

   Only rendered when keda.scaledObjects is defined and non-empty.
   ============================================================================ #}
{% if keda is defined and keda.scaledObjects | default([]) %}
{% set prom = keda.prometheus | default({}) %}
{% set auth_mode = prom.authMode | default('none') %}
{% set prom_addr = (prom.baseUrl | default('http://prometheus:9090')) ~ ':' ~ (prom.port | default(9090)) %}
{% for so in keda.scaledObjects %}
{% set target_name = so.targetRef.name if so.targetRef is defined and so.targetRef.name else model_id_label ~ '-decode' %}
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: {{ so.name }}
  namespace: {{ namespace.name }}
  annotations:
    llm-d.ai/managed: "true"
    llm-d.ai/model-id: "{{ model.name }}"
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: {{ so.targetRef.kind | default('Deployment') if so.targetRef is defined else 'Deployment' }}
    name: {{ target_name }}
  minReplicaCount: {{ so.minReplicas | default(1) }}
  maxReplicaCount: {{ so.maxReplicas | default(10) }}
  pollingInterval: {{ so.pollingInterval | default(15) }}
  triggers:
{% for trigger in so.triggers | default([]) %}
  - type: {{ trigger.type }}
{% if trigger.name is defined %}
    name: {{ trigger.name }}
{% endif %}
    metricType: {{ trigger.metricType | default('AverageValue') }}
{% if auth_mode == 'bearer-secret' %}
    authenticationRef:
      name: keda-prometheus-auth
{% endif %}
    metadata:
      serverAddress: "{{ prom_addr }}"
{% if auth_mode == 'bearer-secret' %}
      authModes: bearer
{% if prom.unsafeSsl | default(false) %}
      unsafeSsl: "true"
{% endif %}
{% endif %}
      query: |
        {{ trigger.query | trim | indent(8) }}
      threshold: "{{ trigger.threshold | default('1') }}"
      activationThreshold: "{{ trigger.activationThreshold | default('0') }}"
{% endfor %}
{% if so.behavior is defined %}
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
{{ so.behavior | toyaml | indent(6, first=True) }}
{% endif %}
{% endfor %}
{% endif %}
```

- [ ] **Step 2: Write `32_keda-triggerauthentication.yaml.j2`**

```jinja
{# ============================================================================
   32_keda-triggerauthentication.yaml.j2

   KEDA TriggerAuthentication for keda.prometheus.authMode=bearer-secret.
   References a user-supplied Secret (must be in the deploy namespace).
   The Secret must contain keys: bearerToken, ca.crt.

   Only rendered when keda.prometheus.authMode == 'bearer-secret'.
   ============================================================================ #}
{% if keda is defined and keda.prometheus is defined and keda.prometheus.authMode | default('none') == 'bearer-secret' %}
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: keda-prometheus-auth
  namespace: {{ namespace.name }}
spec:
  secretTargetRef:
  - parameter: bearerToken
    name: {{ keda.prometheus.secretName }}
    key: bearerToken
{% if not (keda.prometheus.unsafeSsl | default(false)) %}
  - parameter: ca
    name: {{ keda.prometheus.secretName }}
    key: ca.crt
{% endif %}
{% endif %}
```

- [ ] **Step 3: Verify the templates render correctly by spot-checking with the render pipeline**

Register the new templates in the render pipeline's template list. Check where existing templates are registered:

```bash
grep -rn "30_epp-keda\|29_epp-keda\|28_wva" llmdbenchmark/parser/ | head -20
```

Add `31_keda-scaledobjects` and `32_keda-triggerauthentication` at the same registration point in the same order.

- [ ] **Step 4: Commit**

```bash
git add config/templates/jinja/31_keda-scaledobjects.yaml.j2 \
        config/templates/jinja/32_keda-triggerauthentication.yaml.j2
git commit -m "feat: add Jinja templates for generic KEDA ScaledObjects and TriggerAuthentication"
```

---

## Task 3: Render pipeline integration — register templates and add scenario test

**Files:**
- Modify: whichever file registers Jinja templates in `llmdbenchmark/parser/` (found in Task 2 Step 3)
- Create: `tests/test_keda_scaledobjects_render.py`

**Interfaces:**
- Consumes: `31_keda-scaledobjects.yaml.j2`, `32_keda-triggerauthentication.yaml.j2`
- Produces: rendered YAML files in the stack output directory, readable by `_find_yaml`

- [ ] **Step 1: Find and read the template registration file**

```bash
grep -rn "30_epp-keda-saturation-scaledobject\|29_epp-keda" llmdbenchmark/parser/ --include="*.py" -l
```

Read the file. Find the list or dict that maps template filenames. Add the two new templates at the end of the list in the same style as the existing entries.

- [ ] **Step 2: Write render integration tests**

Look at an existing render test for the pattern (e.g. `tests/test_accelerator_profiles.py` or `tests/test_scenario_sections.py`). Then write:

```python
# tests/test_keda_scaledobjects_render.py
"""Render-pipeline integration tests for templates 31 and 32.

Verifies that:
- A scenario with keda.scaledObjects renders 31_keda-scaledobjects.yaml
- authMode=none renders no TriggerAuthentication and no authenticationRef
- authMode=bearer-secret renders 32_keda-triggerauthentication.yaml
  and adds authenticationRef to each prometheus trigger
- A scenario without keda.scaledObjects renders neither template
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Import the same render helper used by other render tests — e.g.:
# from tests.fixtures.<whatever> import render_scenario
# Discover the correct import by reading an existing render test.
```

The test bodies must:
1. Call the render helper with a minimal scenario dict that sets `keda.scaledObjects`
2. Assert the rendered `31_keda-scaledobjects.yaml` exists and is non-empty
3. Parse the YAML; for `authMode=none` assert no `authenticationRef` key in any trigger
4. For `authMode=bearer-secret` assert `authenticationRef.name == "keda-prometheus-auth"` in every trigger, and `32_keda-triggerauthentication.yaml` exists with `spec.secretTargetRef[0].name == secretName`
5. For a scenario without `keda.scaledObjects` assert neither file exists (or is empty)

Discover the render helper signature by reading `tests/test_accelerator_profiles.py`:

```bash
head -80 tests/test_accelerator_profiles.py
```

- [ ] **Step 3: Run render tests**

```bash
pytest tests/test_keda_scaledobjects_render.py -v
```
Expected: all pass.

- [ ] **Step 4: Run the full test suite to check for regressions**

```bash
pytest tests/ -x -q -n2
```
Expected: only the pre-existing `test_xpu_profile_keeps_precise_router_compact_and_token_optional` failure (unrelated to this change); all others pass.

- [ ] **Step 5: Commit**

```bash
git add llmdbenchmark/parser/<registration-file>.py tests/test_keda_scaledobjects_render.py
git commit -m "feat: register keda ScaledObjects templates in render pipeline"
```

---

## Task 4: Wire `step_03` — call `_install_keda_if_enabled` unconditionally

**Files:**
- Modify: `llmdbenchmark/standup/steps/step_03_workload_monitoring.py:589-661`

**Interfaces:**
- Consumes: `keda.stacks_enabling_keda`, `keda.install_keda_for_namespace`
- Produces: TriggerAuthentication and ScaledObjects applied in the target namespace during standup

- [ ] **Step 1: Write the failing test**

Add to `tests/test_standup_keda.py`:

```python
# ---------------------------------------------------------------------------
# Tests: step_03 integration — _install_keda_if_enabled
# ---------------------------------------------------------------------------

from llmdbenchmark.standup.steps.step_03_workload_monitoring import WorkloadMonitoringStep


@dataclass
class _FullStubContext:
    rendered_stacks: list[Path] = field(default_factory=list)
    is_openshift: bool = False   # deliberately False to test non-OCP path
    platform_type: str = "kind"
    logger: _StubLogger = field(default_factory=_StubLogger)
    dry_run: bool = False
    non_admin: bool = False


class TestInstallKedaIfEnabled:
    def test_runs_on_non_openshift(self, tmp_path: Path) -> None:
        """_install_keda_if_enabled runs even when is_openshift is False."""
        stack = _write_stack(tmp_path, "s1", cfg={
            "keda": {
                "prometheus": {"authMode": "none"},
                "scaledObjects": [{"name": "x"}],
            },
            "namespace": {"name": "ns1"},
        })
        _write_so_template(stack)
        step = WorkloadMonitoringStep()
        ctx = _FullStubContext(rendered_stacks=[stack], is_openshift=False)
        cmd = _StubCmd()
        errors: list = []

        step._install_keda_if_enabled(cmd, ctx, errors)

        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        assert len(applied) == 1, (
            f"Expected ScaledObjects apply on non-OCP; kube_calls={cmd.kube_calls}"
        )

    def test_no_keda_stacks_is_noop(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", cfg={"namespace": {"name": "ns1"}})
        step = WorkloadMonitoringStep()
        ctx = _FullStubContext(rendered_stacks=[stack])
        cmd = _StubCmd()
        errors: list = []

        step._install_keda_if_enabled(cmd, ctx, errors)

        assert cmd.kube_calls == []
        assert errors == []
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_standup_keda.py::TestInstallKedaIfEnabled -v
```
Expected: `AttributeError: 'WorkloadMonitoringStep' has no attribute '_install_keda_if_enabled'`.

- [ ] **Step 3: Add `_install_keda_if_enabled` to `step_03` and call it from `execute`**

In `llmdbenchmark/standup/steps/step_03_workload_monitoring.py`:

1. Add import at the top of the file (alongside existing `keda_saturation` import):
   ```python
   from llmdbenchmark.standup import keda as keda_mod
   ```

2. In `execute()`, after the existing `if context.is_openshift and self._is_modelservice(context):` block (around line 71), add an unconditional call:
   ```python
   self._install_keda_if_enabled(cmd, context, errors)
   ```

3. Add the method (after `_install_epp_keda_saturation_if_enabled`, around line 661):
   ```python
   def _install_keda_if_enabled(
       self,
       cmd: CommandExecutor,
       context: ExecutionContext,
       errors: list,
   ) -> None:
       """Apply TriggerAuthentication and ScaledObjects for stacks with keda.scaledObjects.

       Runs on any platform (not gated on is_openshift). One install call per
       unique keda namespace — namespace comes from namespace.name in config.yaml.
       """
       pairs = keda_mod.stacks_enabling_keda(context.rendered_stacks or [])
       if not pairs:
           return

       seen_namespaces: set[str] = set()
       for stack_path, cfg in pairs:
           ns = cfg.get("namespace", {}).get("name", "")
           if not ns or ns in seen_namespaces:
               continue
           seen_namespaces.add(ns)
           context.logger.log_info(
               f"🎯 Setting up generic KEDA ScaledObjects for ns/{ns}"
           )
           keda_mod.install_keda_for_namespace(
               cmd=cmd,
               context=context,
               stack_path=stack_path,
               namespace=ns,
               errors=errors,
           )
   ```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_standup_keda.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add llmdbenchmark/standup/steps/step_03_workload_monitoring.py tests/test_standup_keda.py
git commit -m "feat: wire step_03 to install generic KEDA ScaledObjects unconditionally"
```

---

## Task 5: Wire `step_09` — apply ScaledObjects per stack

**Files:**
- Modify: `llmdbenchmark/standup/steps/step_09_deploy_modelservice.py:738-757`

**Interfaces:**
- Consumes: `keda.stacks_enabling_keda`, rendered `31_keda-scaledobjects.yaml` in stack_path
- Produces: ScaledObjects applied per stack after modelservice deploy

- [ ] **Step 1: Write the failing test**

Add to `tests/test_standup_keda.py`:

```python
# ---------------------------------------------------------------------------
# Tests: step_09 integration — _apply_keda_stack_resources
# ---------------------------------------------------------------------------

from llmdbenchmark.standup.steps.step_09_deploy_modelservice import DeployModelserviceStep


class TestApplyKedaStackResources:
    def test_applies_scaledobjects_template(self, tmp_path: Path) -> None:
        stack = tmp_path / "s1"
        stack.mkdir()
        _write_so_template(stack)
        step = DeployModelserviceStep()
        cmd = _StubCmd()
        errors: list = []

        step._apply_keda_stack_resources(cmd, stack, errors)

        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        assert len(applied) == 1
        assert "31_keda-scaledobjects" in applied[0][2]
        assert errors == []

    def test_missing_template_is_noop(self, tmp_path: Path) -> None:
        stack = tmp_path / "s1"
        stack.mkdir()
        step = DeployModelserviceStep()
        cmd = _StubCmd()
        errors: list = []

        step._apply_keda_stack_resources(cmd, stack, errors)

        assert cmd.kube_calls == []
        assert errors == []

    def test_apply_failure_appends_error(self, tmp_path: Path) -> None:
        stack = tmp_path / "s1"
        stack.mkdir()
        _write_so_template(stack)

        @dataclass
        class _FailCmd:
            kube_calls: list = field(default_factory=list)

            def kube(self, *args: str, **_: Any) -> _StubResult:
                self.kube_calls.append(args)
                return _StubResult(success=False, stderr="forbidden")

        step = DeployModelserviceStep()
        errors: list = []

        step._apply_keda_stack_resources(_FailCmd(), stack, errors)

        assert len(errors) == 1
        assert "forbidden" in errors[0]
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_standup_keda.py::TestApplyKedaStackResources -v
```
Expected: `AttributeError: 'DeployModelserviceStep' has no attribute '_apply_keda_stack_resources'`.

- [ ] **Step 3: Add `_apply_keda_stack_resources` to `step_09` and call it from `execute`**

In `llmdbenchmark/standup/steps/step_09_deploy_modelservice.py`:

1. In `execute()`, after the existing `_apply_epp_keda_stack_resources` call (around line 398), add:
   ```python
   epp_keda_config = plan_config.get("eppKedaSaturation", {})
   if epp_keda_config.get("enabled", False) and context.is_openshift:
       self._apply_epp_keda_stack_resources(cmd, stack_path, errors)
       self._log_epp_keda_stack_state(cmd, context, plan_config)

   # Generic KEDA path — not gated on is_openshift
   keda_config = plan_config.get("keda", {})
   if keda_config.get("scaledObjects"):
       self._apply_keda_stack_resources(cmd, stack_path, errors)
   ```

2. Add the method (after `_apply_epp_keda_stack_resources`, around line 757):
   ```python
   def _apply_keda_stack_resources(
       self,
       cmd: CommandExecutor,
       stack_path: Path,
       errors: list,
   ) -> None:
       """Apply per-stack generic KEDA ScaledObjects (template 31).

       TriggerAuthentication was already applied once per namespace by step_03.
       This method only applies the ScaledObjects template for this stack.
       Not gated on is_openshift.
       """
       yaml_path = self._find_yaml(stack_path, "31_keda-scaledobjects")
       if not (yaml_path and self._has_yaml_content(yaml_path)):
           return
       result = cmd.kube("apply", "-f", str(yaml_path), check=False)
       if not result.success:
           errors.append(
               f"Failed to apply keda ScaledObjects for {stack_path.name}: "
               f"{result.stderr}"
           )
   ```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_standup_keda.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Run the full test suite**

```bash
pytest tests/ -x -q -n2
```
Expected: only the pre-existing XPU test failure; all new tests pass.

- [ ] **Step 6: Commit**

```bash
git add llmdbenchmark/standup/steps/step_09_deploy_modelservice.py tests/test_standup_keda.py
git commit -m "feat: wire step_09 to apply generic KEDA ScaledObjects per stack"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| `none` authMode (plain Prometheus, no token) | Task 1 (keda.py), Task 2 (template 31), Task 4 (step_03) |
| `bearer-secret` authMode (user-supplied Secret) | Task 1 (keda.py), Task 2 (template 32), Task 4 (step_03) |
| Multiple ScaledObjects per stack | Task 2 (template 31 loops over list) |
| Generic template — no EPP-specific logic | Task 2 (31 is trigger-agnostic) |
| `eppKedaSaturation` untouched | No modifications to those files in any task |
| Works on non-OpenShift | Task 4 (step_03 called unconditionally), Task 5 (step_09 not gated) |
| `keda` and `eppKedaSaturation` coexist | Different TA name (`keda-prometheus-auth` vs `prometheus-auth`) |

**Template registration gap:** Task 2 Step 3 defers finding the registration file to a grep — this is correct since the file path is discovered at implementation time. The instruction is explicit.

**Type consistency check:** `stacks_enabling_keda` returns `list[tuple[Path, dict]]`, matches what `install_keda_for_namespace` consumes and what step_03's `_install_keda_if_enabled` iterates. `_apply_keda_stack_resources` in step_09 uses `self._find_yaml` and `self._has_yaml_content` from the base `Step` class — consistent with `_apply_epp_keda_stack_resources` pattern.

**No placeholders found.**
