"""Workload profile template renderer.

Replaces REPLACE_ENV_* tokens in .yaml.in profile templates with runtime
values.  Uses a simple regex substitution (not Jinja2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenDef:
    """A REPLACE_ENV_* profile token definition."""

    config_path: (
        str | None
    )  # dotted path into plan config.yaml, or None for runtime-only
    description: str


# Registry of REPLACE_ENV_* tokens used in .yaml.in profile templates.
# Keys are the token suffix (everything after REPLACE_ENV_).
# To add a new token: add an entry here, then add the REPLACE_ENV_<KEY>
# placeholder in the profile template(s) that need it.
PROFILE_TOKENS: dict[str, TokenDef] = {
    "LLMDBENCH_DEPLOY_CURRENT_MODEL": TokenDef(
        config_path="model.name",
        description="Model name being served (e.g. meta-llama/Llama-3.1-8B)",
    ),
    "LLMDBENCH_DEPLOY_CURRENT_TOKENIZER": TokenDef(
        config_path="model.name",
        description="Tokenizer model name (defaults to same as model)",
    ),
    "LLMDBENCH_DEPLOY_CURRENT_MAX_MODEL_LEN": TokenDef(
        config_path="model.maxModelLen",
        description="Model max context length (--max-model-len)",
    ),
    "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL": TokenDef(
        config_path=None,  # runtime: detected in step 02
        description="Model-serving endpoint URL (detected at runtime)",
    ),
    "LLMDBENCH_RUN_DATASET_DIR": TokenDef(
        config_path="experiment.datasetDir",
        description="Dataset directory path or URL",
    ),
    "LLMDBENCH_RUN_DATASET_FILE": TokenDef(
        config_path="experiment.datasetFile",
        description="Dataset filename (basename of the dataset path/URL)",
    ),
}


def _resolve_config_path(config: dict[str, Any], dotted_path: str) -> str:
    """Resolve a dotted path (e.g. 'model.name') in a nested dict. Returns '' if missing."""
    current: Any = config
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return ""
    if current is None:
        return ""
    return str(current)


def build_env_map(
    plan_config: dict[str, Any] | None = None,
    runtime_values: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the REPLACE_ENV_* substitution map.

    Resolves token values from plan_config via the registry, then
    merges runtime_values on top. Empty values are dropped.
    """
    env_map: dict[str, str] = {}

    if plan_config:
        for token_key, token_def in PROFILE_TOKENS.items():
            if token_def.config_path is not None:
                value = _resolve_config_path(plan_config, token_def.config_path)
                if value:
                    env_map[token_key] = value

    # Runtime overrides take precedence
    if runtime_values:
        for key, value in runtime_values.items():
            if value:
                env_map[key] = value

    return env_map


def render_profile(template_content: str, env_map: dict[str, str]) -> str:
    """Replace REPLACE_ENV_{KEY} tokens in template_content. Unknown tokens are left as-is."""

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        return env_map.get(key, match.group(0))

    return re.sub(r"REPLACE_ENV_(\w+)", _replace, template_content)


def render_profile_file(
    source_path: Path,
    dest_path: Path,
    env_map: dict[str, str],
) -> Path:
    """Render a .yaml.in template and write the result to dest_path."""
    template_content = source_path.read_text(encoding="utf-8")
    rendered = render_profile(template_content, env_map)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(rendered, encoding="utf-8")
    return dest_path


# Dotted-key prefixes that flag a workload-treatment override as actually
# being a plan/scenario field. These never match the workload profile YAML,
# so a treatment override on one is a silent no-op; to vary them the user
# wants ``setup.treatments`` or ``kustomize.extraHelmSets`` instead.
# ``classify_override_miss`` returns a sharper hint for these prefixes.
_PLAN_LEVEL_PREFIXES: tuple[str, ...] = (
    "decode.",
    "prefill.",
    "standalone.",
    "modelservice.",
    "fma.",
    "kustomize.",
    "router.",
    "vllmCommon.",
    "model.",
    "scheduler.",  # gentle warning -- could also be the K8s pod scheduler
    "schedulerName",  # top-level K8s pod scheduler
    "gateway.",
    "routing.",
    "storage.",
    "wva.",
    "huggingface.",
)


def classify_override_miss(key: str) -> str:
    """Build a one-line hint for a workload-treatment override that didn't match.

    Two classes:

    - Plan/scenario field. Workload-treatment overrides only touch the
      rendered profile YAML; we point at ``setup.treatments`` or
      ``kustomize.extraHelmSets``.
    - Typo / wrong harness.
    """
    if any(key.startswith(p) for p in _PLAN_LEVEL_PREFIXES):
        return (
            f"override '{key}' looks like a plan/scenario field; "
            "workload-treatment overrides only touch the rendered profile YAML "
            "(load.*, data.*, api.*, etc.). To vary this field, move it to "
            "setup.treatments in an experiment YAML (modelservice/standalone "
            "standups) or kustomize.extraHelmSets (kustomize standups)."
        )
    return (
        f"override '{key}' did not match any path in the workload profile "
        "and was silently dropped. Check the profile YAML for the correct "
        "dotted path (typo? wrong harness?)."
    )


_MISSING = object()


def _descend(container: Any, part: str) -> Any:
    """Step one level into a dict key or a list index.

    Returns the child node, or ``_MISSING`` if ``part`` doesn't address an
    existing element. Integer-looking ``part`` values index into lists, so
    dotted paths can traverse list elements.
    """
    if isinstance(container, dict):
        return container[part] if part in container else _MISSING
    if isinstance(container, list):
        try:
            idx = int(part)
        except ValueError:
            return _MISSING
        if -len(container) <= idx < len(container):
            return container[idx]
    return _MISSING


def _assign(container: Any, part: str, value: Any) -> bool:
    """Set ``part`` on the parent ``container`` (dict key or list index).

    Returns True on success. A list index must already be in range (we set,
    never append/grow); a bad index or non-container parent fails.
    """
    if isinstance(container, dict):
        container[part] = value
        return True
    if isinstance(container, list):
        try:
            idx = int(part)
        except ValueError:
            return False
        if -len(container) <= idx < len(container):
            container[idx] = value
            return True
    return False


def apply_overrides(
    profile_content: str, overrides: dict[str, Any]
) -> tuple[str, list[str]]:
    """Apply dotted key=value overrides to a rendered YAML profile.

    Parses the YAML, walks dotted keys to set values, and re-dumps. Path
    segments address dict keys or (when integer-looking) list indices.

    Returns ``(rendered_content, unmatched_keys)``, where ``unmatched_keys``
    are override keys whose dotted path did not exist in the profile. The
    caller is expected to log a warning per unmatched key (see
    :func:`classify_override_miss` for a pre-built hint).

    Falls back to ``(original_content, [])`` if YAML parsing fails.
    """
    import yaml  # pylint: disable=import-outside-toplevel

    try:
        data = yaml.safe_load(profile_content)
        if not isinstance(data, dict):
            return profile_content, []

        # An override is "unmatched" when a PARENT key along its dotted path
        # doesn't exist, so we can't reach the leaf to write to. A missing
        # leaf on a dict parent is still allowed (add-new-field overrides);
        # a list parent requires an in-range index (see _assign).
        unmatched: list[str] = []
        for key, value in overrides.items():
            parts = key.split(".")
            target = data
            parent_chain_intact = True
            for part in parts[:-1]:
                target = _descend(target, part)
                if target is _MISSING:
                    parent_chain_intact = False
                    break
            if not parent_chain_intact or not _assign(
                target, parts[-1], _coerce_value(value)
            ):
                unmatched.append(key)

        return (
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            unmatched,
        )
    except yaml.YAMLError:
        return profile_content, []


def _coerce_value(value: Any) -> Any:
    """Coerce a string to int, float, bool, or leave as str. Non-string values are returned unchanged."""
    if not isinstance(value, str):
        return value
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
