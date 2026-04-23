"""Conversion layer from discovery components to benchmark report v0.2 schema."""

import hashlib
import json
import logging
import re
import shlex
from collections import defaultdict
from typing import Any

from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import (
    Component as ReportComponent,
)
from llmdbenchmark.analysis.benchmark_report.schema_v0_2_components import (
    HostType,
)

from ..models.components import Component as DiscoveryComponent, DiscoveryResult

logger = logging.getLogger(__name__)


def _cfg_id(standardized_dict: dict, native_dict: dict) -> str:
    """Compute a deterministic configuration ID from standardized + native dicts.

    Args:
        standardized_dict: Serialised standardized section
        native_dict: Serialised native section

    Returns:
        Hex SHA-256 digest (first 16 characters)
    """
    blob = json.dumps(
        {"standardized": standardized_dict, "native": native_dict},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _extract_vllm_serve_tokens(raw_args: list) -> list[str]:
    """Extract tokenized vllm serve arguments from raw pod args.

    Handles two formats:
    1. Pre-tokenized arg lists (e.g. ["--model", "llama"]) -- returned as-is.
    2. Single shell script strings containing ``vllm serve`` -- the portion
       after ``vllm serve`` is extracted, line continuations are collapsed,
       and the result is split into tokens with :func:`shlex.split`.

    Args:
        raw_args: Raw args list from the pod container spec.

    Returns:
        List of argument tokens suitable for flag/value parsing.
    """
    if not raw_args:
        return []

    # Already a proper token list
    if len(raw_args) > 1 or raw_args[0].startswith("--"):
        return raw_args

    # Single string -- look for 'vllm serve'
    script = raw_args[0]
    match = re.search(r"vllm\s+serve\s+", script)
    if not match:
        return raw_args

    vllm_portion = script[match.end() :]

    # Collapse shell line continuations (backslash + newline)
    vllm_portion = vllm_portion.replace("\\\n", " ")

    # Trim at first bare newline or semicolon (end of the vllm command)
    for end_char in ("\n", ";"):
        idx = vllm_portion.find(end_char)
        if idx != -1:
            vllm_portion = vllm_portion[:idx]

    try:
        return shlex.split(vllm_portion)
    except ValueError:
        return raw_args


def _resolve_env_ref(value: str, env_lookup: dict[str, str]) -> str:
    """Resolve environment variable references in a string value.

    Handles ``$VAR`` and ``${VAR}`` patterns.  Unknown variables are left
    as-is so the caller can see something was unresolved.

    Args:
        value: String that may contain env-var references.
        env_lookup: Mapping of variable names to values.

    Returns:
        String with known references replaced.
    """
    if not value or "$" not in value:
        return value

    def _replacer(m: re.Match) -> str:
        var_name = m.group(1) or m.group(2)
        return env_lookup.get(var_name, m.group(0))

    return re.sub(r"\$\{(\w+)\}|\$(\w+)", _replacer, value)


def _resolve_model_name(
    component: DiscoveryComponent,
    default: str = "unknown",
) -> str:
    """Resolve the model name from a vLLM discovery component.

    Tries multiple sources in priority order:
    1. ``vllm_config["model"]`` from the collector's flag parsing
    2. ``--model`` flag from raw CLI args (with env-var resolution)
    3. ``--served-model-name`` flag from raw CLI args
    4. Positional model argument from ``vllm serve <model>``
    5. Provided *default*

    Args:
        component: Discovery component with vLLM native data.
        default: Fallback value when no model name can be found.

    Returns:
        Resolved model name string.
    """
    native = component.native or {}
    vllm_config = native.get("vllm_config", {})

    # Source 1: collector's extraction
    model = vllm_config.get("model")
    if model:
        return model

    # Build env-var lookup for resolving references
    env_lookup = {}
    for env in native.get("environment", []):
        name = env.get("name", "")
        value = env.get("value")
        if value and value != "<REDACTED>":
            env_lookup[name] = value

    # Parse tokens from raw args
    tokens = _extract_vllm_serve_tokens(native.get("args", []))

    # Source 2 & 3: --model or --served-model-name flags from tokens
    if tokens:
        i = 0
        while i < len(tokens):
            arg = tokens[i]
            if arg.startswith("--"):
                flag = arg[2:]
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    if flag in ("model", "served-model-name"):
                        resolved = _resolve_env_ref(tokens[i + 1], env_lookup)
                        if resolved:
                            return resolved
                    i += 2
                else:
                    i += 1
            else:
                i += 1

    # Source 4: positional model arg (first non-flag token)
    if tokens and not tokens[0].startswith("-"):
        positional = _resolve_env_ref(tokens[0], env_lookup)
        if positional:
            return positional

    return default


def _build_native_dict(component: DiscoveryComponent) -> dict[str, Any]:
    """Build a ComponentNative-compatible dict from a discovery component.

    Args:
        component: Discovery component

    Returns:
        Dict with args, envars, config keys
    """
    native = component.native or {}

    # For vLLM pods, extract structured native data
    if component.tool == "vllm":
        args = {}

        # Build env-var lookup for resolving $VAR references in flag values
        env_lookup = {}
        envars = {}
        for env in native.get("environment", []):
            name = env.get("name", "")
            value = env.get("value")
            if value and value != "<REDACTED>":
                env_lookup[name] = value
                envars[name] = value

        # Parse CLI flags from args list (handles shell script strings)
        tokens = _extract_vllm_serve_tokens(native.get("args", []))
        if tokens:
            i = 0
            while i < len(tokens):
                arg = tokens[i]
                if arg.startswith("--"):
                    flag = arg[2:]
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                        args[flag] = _resolve_env_ref(tokens[i + 1], env_lookup)
                        i += 2
                    else:
                        args[flag] = None
                        i += 1
                else:
                    i += 1

        return {
            "args": args if args else None,
            "envars": envars if envars else None,
            "config": None,
        }

    # For gaie-controller, parse args and envars from native data
    if component.tool == "gaie-controller":
        args = {}
        envars = {}

        tokens = native.get("args", [])
        if tokens:
            i = 0
            while i < len(tokens):
                arg = tokens[i]
                if arg.startswith("--"):
                    flag = arg[2:]
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                        args[flag] = tokens[i + 1]
                        i += 2
                    else:
                        args[flag] = None
                        i += 1
                else:
                    i += 1

        for env in native.get("environment", []):
            name = env.get("name", "")
            value = env.get("value")
            if value and value != "<REDACTED>":
                envars[name] = value

        controller_config = native.get("controller_config", {})
        config_map_data = controller_config.get("config_map_data")

        return {
            "args": args if args else None,
            "envars": envars if envars else None,
            "config": config_map_data,
        }

    # For generic components, pass through relevant config
    return {
        "args": None,
        "envars": None,
        "config": native if native else None,
    }


def _grouping_key(component: DiscoveryComponent) -> tuple:
    """Create a grouping key for replica aggregation.

    vLLM pods with the same model, role, and config are grouped together
    and counted as replicas.

    Args:
        component: Discovery component

    Returns:
        Hashable grouping key tuple
    """
    native = component.native or {}
    vllm_config = native.get("vllm_config", {})

    model = _resolve_model_name(component, default="")
    role = str(native.get("role", "replica"))

    # Config-relevant fields for grouping
    tp = vllm_config.get("tensor_parallel_size", 1)
    pp = vllm_config.get("pipeline_parallel_size", 1)
    dp = vllm_config.get("data_parallel_size", 1)
    dp_local = vllm_config.get("data_local_parallel_size", 1)
    workers = vllm_config.get("num_workers", 1)
    ep = vllm_config.get("expert_parallel_size", 1)

    gpu = native.get("gpu", {})
    gpu_model = gpu.get("model", "unknown")
    gpu_count = gpu.get("count", 0)

    return (model, role, tp, pp, dp, dp_local, workers, ep, gpu_model, gpu_count)


def _vllm_to_inference_engine_dict(
    components: list[DiscoveryComponent], index: int
) -> dict[str, Any]:
    """Convert a group of identical vLLM pods to an InferenceEngine dict.

    Args:
        components: List of identical vLLM discovery components (same config)
        index: Index for label generation

    Returns:
        Dict compatible with schema_v0_2.Component
    """
    representative = components[0]
    native = representative.native or {}
    vllm_config = native.get("vllm_config", {})
    gpu = native.get("gpu", {})
    role = native.get("role", HostType.REPLICA)

    # Ensure role is a HostType
    if isinstance(role, str):
        role_map = {
            "prefill": HostType.PREFILL,
            "decode": HostType.DECODE,
            "replica": HostType.REPLICA,
        }
        role = role_map.get(role.lower(), HostType.REPLICA)

    model_name = _resolve_model_name(representative)
    gpu_model = gpu.get("model", "unknown")
    gpu_count = gpu.get("count", 0)

    tp = vllm_config.get("tensor_parallel_size", 1)
    pp = vllm_config.get("pipeline_parallel_size", 1)
    dp = vllm_config.get("data_parallel_size", 1)
    dp_local = vllm_config.get("data_local_parallel_size", 1)
    workers = vllm_config.get("num_workers", 1)
    ep = vllm_config.get("expert_parallel_size", 1)

    standardized = {
        "kind": "inference_engine",
        "tool": representative.tool or "vllm",
        "tool_version": representative.tool_version or "unknown",
        "role": role,
        "replicas": len(components),
        "model": {"name": model_name},
        "accelerator": {
            "model": gpu_model,
            "count": gpu_count,
            "parallelism": {
                "tp": tp,
                "pp": pp,
                "dp": dp,
                "dp_local": dp_local,
                "workers": workers,
                "ep": ep,
            },
        },
    }

    native_dict = _build_native_dict(representative)

    cfg = _cfg_id(standardized, native_dict)
    label = f"vllm-{role}-{index}"

    return {
        "metadata": {
            "label": label,
            "cfg_id": cfg,
        },
        "standardized": standardized,
        "native": native_dict,
    }


def _generic_component_dict(
    component: DiscoveryComponent,
) -> dict[str, Any]:
    """Convert a non-vLLM discovery component to a Generic dict.

    For gaie-controller components, produces a richer output matching the EPP
    pattern from native_to_br0_2.py (_add_inference_scheduler_component).

    Args:
        component: Discovery component

    Returns:
        Dict compatible with schema_v0_2.Component
    """
    # Special handling for GAIE controller (EPP pattern)
    if component.tool == "gaie-controller":
        standardized = {
            "kind": "generic",
            "tool": "request_router",
            "tool_version": component.tool_version or "unknown",
        }

        native_dict = _build_native_dict(component)

        cfg = _cfg_id(standardized, native_dict)
        label = "EPP"

        return {
            "metadata": {
                "label": label,
                "cfg_id": cfg,
            },
            "standardized": standardized,
            "native": native_dict,
        }

    standardized = {
        "kind": "generic",
        "tool": component.tool or component.metadata.kind.lower(),
        "tool_version": component.tool_version or "unknown",
    }

    native_dict = _build_native_dict(component)

    cfg = _cfg_id(standardized, native_dict)
    label = f"{component.metadata.kind}/{component.metadata.namespace}/{component.metadata.name}"

    return {
        "metadata": {
            "label": label,
            "cfg_id": cfg,
        },
        "standardized": standardized,
        "native": native_dict,
    }


def discovery_to_stack_components(result: DiscoveryResult) -> list[dict]:
    """Convert discovery result to a list of benchmark-report-compatible component dicts.

    For vLLM pods, identical configurations are grouped and counted as replicas.
    For all other components, each becomes a Generic component.

    Args:
        result: DiscoveryResult from stack discovery

    Returns:
        List of dicts, each compatible with schema_v0_2.Component
    """
    output = []

    # Separate vLLM and non-vLLM components
    vllm_components: list[DiscoveryComponent] = []
    other_components: list[DiscoveryComponent] = []

    for component in result.components:
        if component.tool == "vllm":
            vllm_components.append(component)
        else:
            other_components.append(component)

    # Group vLLM components by config for replica aggregation
    groups: dict[tuple, list[DiscoveryComponent]] = defaultdict(list)
    for comp in vllm_components:
        key = _grouping_key(comp)
        groups[key].append(comp)

    # Convert vLLM groups
    for idx, (key, group) in enumerate(groups.items()):
        output.append(_vllm_to_inference_engine_dict(group, idx))

    # Convert other components
    for comp in other_components:
        output.append(_generic_component_dict(comp))

    return output


def discovery_to_scenario_stack(result: DiscoveryResult) -> list[ReportComponent]:
    """Convert discovery result to a list of Pydantic Component objects.

    This constructs actual schema_v0_2.Component instances that can be used
    in a BenchmarkReportV02.scenario.stack field.

    Args:
        result: DiscoveryResult from stack discovery

    Returns:
        List of schema_v0_2.Component Pydantic objects
    """
    component_dicts = discovery_to_stack_components(result)
    return [ReportComponent(**d) for d in component_dicts]
