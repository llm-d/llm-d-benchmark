"""Read GuideLLM's native results file across its report schema versions.

GuideLLM rewrote its output format in the v0.7 CLI refactor
(vllm-project/guidellm#789, #816). Everything the converters used to read at
the top level moved or changed shape:

===========================  ===============================================
report schema v1 (<= v0.6.x) report schema v2 (>= v0.7.0)
===========================  ===============================================
``args``                     ``config`` (a ``BenchmarkScenario``)
``args.data`` -> list[str]   ``config.spec.data`` -> list[dict]
``args.profile`` -> str      ``config.spec.profile.kind`` -> str
``args.rate`` -> list[float] ``config.spec.profile.rate``/``.streams``
``args.model``               ``config.spec.backend.model``
===========================  ===============================================

The per-benchmark ``benchmarks[i].metrics.*`` tree did *not* change, so the
bulk of the converters still applies; only the run-wide configuration needs
version-aware reading, which is what this module provides.

Where a value exists both run-wide and per-benchmark, prefer the
per-benchmark copy: ``benchmarks[i].config.strategy`` is the *realized*
scheduling strategy for that stage, which is both unambiguous (no indexing
into a run-wide rate list, whose length need not match the benchmark count)
and stable further back than the v2 rewrite. ``benchmarks[i].config`` has
carried ``strategy``/``backend`` since at least v0.6.1.
"""

from __future__ import annotations

import sys
from typing import Any

import yaml

from .core import get_nested


def report_config(data: dict) -> dict:
    """Get the run-wide GuideLLM configuration block.

    Args:
        data (dict): Parsed GuideLLM results file.

    Returns:
        dict: ``config`` on report schema v2, ``args`` on v1, else empty.
    """
    for key in ("config", "args"):
        block = data.get(key)
        if isinstance(block, dict):
            return block
    return {}


def data_args(data: dict) -> dict:
    """Get the first dataset specification as a dict.

    On report schema v2 the sources are already deserialized dicts under
    ``config.spec.data``; on v1 they were the raw ``--data`` strings under
    ``args.data`` and still need parsing.

    Args:
        data (dict): Parsed GuideLLM results file.

    Returns:
        dict: First dataset specification, or empty if none is readable.
    """
    config = report_config(data)

    sources = get_nested(config, ["spec", "data"])
    if sources is None:
        sources = config.get("data")
    if not isinstance(sources, list) or not sources:
        return {}

    if len(sources) > 1:
        sys.stderr.write(
            "WARNING: Multiple data sources not supported in conversion, will"
            " only record first source\n"
        )

    first = sources[0]
    if isinstance(first, str):
        try:
            first = yaml.safe_load(first)
        except yaml.YAMLError:
            return {}
    return first if isinstance(first, dict) else {}


def strategy(results: dict) -> dict:
    """Get the realized scheduling strategy for a single benchmark.

    Args:
        results (dict): One entry of the report's ``benchmarks`` list.

    Returns:
        dict: ``config.strategy``, e.g.
            ``{"type_": "concurrent", "streams": 300, ...}``, or empty.
    """
    return get_nested(results, ["config", "strategy"], {}) or {}


def profile_kind(data: dict, results: dict | None = None) -> str | None:
    """Get the scheduling profile kind, e.g. ``"concurrent"``.

    Args:
        data (dict): Parsed GuideLLM results file.
        results (dict): One entry of ``benchmarks``, preferred when given.

    Returns:
        str: Profile kind, or None if it is not recorded.
    """
    if results:
        # The realized strategy names each stage of a multi-stage profile
        # (e.g. a sweep stage reports "synchronous"/"throughput"/"constant").
        kind = strategy(results).get("type_")
        if isinstance(kind, str) and kind:
            return kind

    config = report_config(data)
    # Report schema v2 nests the profile; v1 had a flat profile name string.
    kind = get_nested(config, ["spec", "profile", "kind"])
    if not isinstance(kind, str) or not kind:
        kind = config.get("profile")
    return kind if isinstance(kind, str) and kind else None


def rate_and_concurrency(
    data: dict, results: dict, index: int = 0
) -> tuple[float | None, int | None]:
    """Get the request rate and concurrency for a single benchmark.

    Reads the realized ``config.strategy`` for the benchmark, which records
    what the scheduler actually ran rather than what was requested. Falls
    back to indexing the run-wide profile for reports that predate
    per-benchmark strategies.

    Args:
        data (dict): Parsed GuideLLM results file.
        results (dict): One entry of the report's ``benchmarks`` list.
        index (int): Index of ``results`` within ``benchmarks``.

    Returns:
        tuple: ``(rate_qps, concurrency)``, either of which may be None when
            the profile does not constrain that dimension.
    """
    strat = strategy(results)
    kind = profile_kind(data, results)

    if kind in ("async", "constant", "poisson"):
        rate = strat.get("rate")
        if rate is None:
            rate = _run_wide_rate(data, ["rate"], index)
        return (float(rate) if rate is not None else None), None

    if kind == "concurrent":
        streams = strat.get("streams", strat.get("max_concurrency"))
        if streams is None:
            streams = _run_wide_rate(data, ["streams", "rate"], index)
        return None, (int(streams) if streams is not None else None)

    if kind == "throughput":
        # Throughput is unrated by construction; its only bound is the
        # optional concurrency cap, which the scheduler reports as the
        # worker count when no explicit cap was set.
        cap = strat.get("max_concurrency", strat.get("worker_count"))
        if cap is None:
            cap = _run_wide_rate(data, ["max_concurrency", "rate"], index)
        return None, (int(cap) if cap is not None else None)

    if kind == "synchronous":
        return None, 1

    return None, None


def _run_wide_rate(data: dict, keys: list[str], index: int) -> Any:
    """Index into a run-wide profile rate list, for pre-v0.7 reports.

    Args:
        data (dict): Parsed GuideLLM results file.
        keys (list): Candidate field names holding the rate list.
        index (int): Benchmark index to select.

    Returns:
        Any: Selected rate value, or None if unavailable.
    """
    config = report_config(data)
    for key in keys:
        values = get_nested(config, ["spec", "profile", key])
        if values is None:
            values = config.get(key)
        if isinstance(values, (int, float)):
            return values
        if isinstance(values, list) and index < len(values):
            return values[index]
    return None


def prefix(input_args: dict) -> dict | None:
    """Build the standardized prefix description from dataset arguments.

    GuideLLM v0.7 replaced the flat ``prefix_tokens``/``prefix_count`` pair
    with ``prefix_buckets``, a weighted list. The standardized schema
    describes only a single prefix length, so a multi-bucket configuration
    cannot be represented; report the first bucket and warn.

    Args:
        input_args (dict): Dataset specification, as returned by data_args.

    Returns:
        dict: Fields for LoadPrefix, or None when no prefix is configured.
    """
    buckets = input_args.get("prefix_buckets")
    if isinstance(buckets, list) and buckets:
        if len(buckets) > 1:
            sys.stderr.write(
                "WARNING: Multiple prefix_buckets not supported in"
                " conversion, will only record first bucket. Utilize native"
                " section to properly capture.\n"
            )
        bucket = buckets[0] if isinstance(buckets[0], dict) else {}
        prefix_tokens = bucket.get("prefix_tokens")
        prefix_count = bucket.get("prefix_count")
    else:
        # Report schema v1 carried these on the dataset directly.
        prefix_tokens = input_args.get("prefix_tokens")
        prefix_count = input_args.get("prefix_count")

    if not prefix_tokens:
        return None

    return {
        "prefix_len": {
            "distribution": "fixed",
            "value": prefix_tokens,
        },
        "num_groups": 1,
        "num_users_per_group": 1,
        "num_prefixes": prefix_count or 1,
    }


def multi_turn(input_args: dict) -> dict | None:
    """Build the standardized multi-turn description from dataset arguments.

    Args:
        input_args (dict): Dataset specification, as returned by data_args.

    Returns:
        dict: Fields for MultiTurn, or None for single-turn workloads.
    """
    turns = input_args.get("turns")
    if not isinstance(turns, int) or turns <= 1:
        return None

    return {
        "enabled": True,
        "max_turns": {"distribution": "fixed", "value": turns},
    }


def source(input_args: dict) -> str:
    """Determine how input tokens for the workload were produced.

    Args:
        input_args (dict): Dataset specification, as returned by data_args.

    Returns:
        str: A LoadSource value.
    """
    kind = input_args.get("kind")
    if kind == "synthetic_text":
        return "random"
    if kind:
        # Every other registered deserializer reads prompts from a dataset,
        # a file, or an in-memory collection.
        return "sampled"
    # Report schema v1 datasets had no kind; infer from a dataset reference.
    if "source" in input_args:
        return "sampled"
    if "prompt_tokens" in input_args:
        return "random"
    return "unknown"


def model(data: dict, results: dict | None = None) -> str | None:
    """Get the model name under test.

    Args:
        data (dict): Parsed GuideLLM results file.
        results (dict): One entry of ``benchmarks``, preferred when given.

    Returns:
        str: Model name, or None if it is not recorded.
    """
    candidates: list[Any] = []
    if results:
        # Per-benchmark backend config (report schema v2, and v0.6.x).
        candidates.append(get_nested(results, ["config", "backend", "model"]))
    config = report_config(data)
    candidates.append(get_nested(config, ["spec", "backend", "model"]))
    # Report schema v1 kept the model on the flat args block.
    candidates.append(config.get("model"))

    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
