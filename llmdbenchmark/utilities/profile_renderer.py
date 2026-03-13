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

    config_path: str | None  # dotted path into plan config.yaml, or None for runtime-only
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
    "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL": TokenDef(
        config_path=None,  # runtime: detected in step 02
        description="Model-serving endpoint URL (detected at runtime)",
    ),
    "LLMDBENCH_RUN_DATASET_DIR": TokenDef(
        config_path="experiment.datasetDir",
        description="Dataset directory path or URL",
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


def apply_overrides(profile_content: str, overrides: dict[str, str]) -> str:
    """Apply dotted key=value overrides to a rendered YAML profile.

    Parses the YAML, walks dotted keys to set values, and re-dumps.
    Falls back to the original content if YAML parsing fails.
    """
    import yaml  # pylint: disable=import-outside-toplevel

    try:
        data = yaml.safe_load(profile_content)
        if not isinstance(data, dict):
            return profile_content

        for key, value in overrides.items():
            parts = key.split(".")
            target = data
            for part in parts[:-1]:
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    target = None
                    break
            if isinstance(target, dict):
                target[parts[-1]] = _coerce_value(value)

        return yaml.dump(data, default_flow_style=False, sort_keys=False)
    except yaml.YAMLError:
        return profile_content


def _coerce_value(value: str):
    """Coerce a string to int, float, bool, or leave as str."""
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
