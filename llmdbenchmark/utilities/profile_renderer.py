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
    default: str | None = None  # fallback when no config/runtime value resolves


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
    "LLMDBENCH_RUN_DATASET_FILE": TokenDef(
        config_path="experiment.datasetFile",
        description="Dataset filename (basename of the dataset path/URL)",
    ),
    "LLMDBENCH_RUN_NUM_REQUESTS": TokenDef(
        config_path="experiment.numRequests",
        description="Total requests for the load stage",
        default="192",
    ),
    "LLMDBENCH_RUN_CONCURRENCY": TokenDef(
        config_path="experiment.concurrency",
        description="Concurrency level (also used as num_conversations)",
        default="32",
    ),
    "LLMDBENCH_RUN_SEED": TokenDef(
        config_path="experiment.seed",
        description="Random seed for conversation_replay data generation",
        default="42",
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

    Resolves token values from plan_config via the registry, falling back
    to each token's registered default, then merges runtime_values on top.
    Empty values are dropped.
    """
    env_map: dict[str, str] = {}

    for token_key, token_def in PROFILE_TOKENS.items():
        value = ""
        if plan_config and token_def.config_path is not None:
            value = _resolve_config_path(plan_config, token_def.config_path)
        if not value and token_def.default is not None:
            value = token_def.default
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
# being a plan/scenario field. Putting any of these under top-level
# ``treatments:`` (or ``--overrides``) is a silent no-op today -- the value
# lands in a corner of the override dict that ``apply_overrides`` walks
# against the workload profile YAML, where the key path never exists.
#
# To actually vary these fields, the user wants ``setup.treatments`` in an
# experiment YAML (modelservice/standalone), or ``kustomize.extraHelmSets``
# (kustomize). ``classify_override_miss`` returns a sharper hint when it
# sees one of these prefixes.
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


def apply_overrides(
    profile_content: str, overrides: dict[str, str]
) -> tuple[str, list[str]]:
    """Apply dotted key=value overrides to a rendered YAML profile.

    Parses the YAML, walks dotted keys to set values, and re-dumps.

    Returns ``(rendered_content, unmatched_keys)``. ``unmatched_keys`` is the
    list of override keys whose dotted path did not exist in the profile --
    they were silently dropped under the old API. The caller is expected to
    log a warning per unmatched key (see :func:`classify_override_miss` for a
    pre-built hint).

    Falls back to ``(original_content, [])`` if YAML parsing fails (we can't
    even tell what would have matched).
    """
    import yaml  # pylint: disable=import-outside-toplevel

    try:
        data = yaml.safe_load(profile_content)
        if not isinstance(data, dict):
            return profile_content, []

        # An override is "unmatched" when one of the PARENT keys along its
        # dotted path doesn't exist -- in that case we can't even reach the
        # leaf to write to, and silently dropping it is the bug we're
        # warning about. A missing LEAF is still allowed (intentional
        # add-new-field overrides keep working), but in practice a missing
        # leaf with all parents present is unusual; we don't warn on it.
        unmatched: list[str] = []
        for key, value in overrides.items():
            parts = key.split(".")
            target = data
            parent_chain_intact = True
            for part in parts[:-1]:
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    parent_chain_intact = False
                    break
            if parent_chain_intact and isinstance(target, dict):
                target[parts[-1]] = _coerce_value(value)
            else:
                unmatched.append(key)

        return (
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            unmatched,
        )
    except yaml.YAMLError:
        return profile_content, []


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
