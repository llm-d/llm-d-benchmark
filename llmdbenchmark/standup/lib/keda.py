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
        ta_yaml = _find_yaml(stack_path, "27a_keda-triggerauthentication")
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
                f"keda TriggerAuthentication template (27a_keda-triggerauthentication) "
                f"not found for ns/{namespace}. KEDA bearer-secret auth will not work."
            )

    so_yaml = _find_yaml(stack_path, "27_keda-scaledobjects")
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
