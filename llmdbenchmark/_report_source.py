"""Refuse an llmd-benchmark-report that was not installed from a package index."""

import json
import os
from importlib import metadata

DISTRIBUTION = "llmd-benchmark-report"
OVERRIDE_ENV = "LLMDBENCH_ALLOW_LOCAL_BENCHMARK_REPORT"


def local_install_url(dist: metadata.Distribution | None = None) -> str | None:
    """Return where llmd-benchmark-report was installed from, or None for an index install."""
    if dist is None:
        try:
            dist = metadata.distribution(DISTRIBUTION)
        except metadata.PackageNotFoundError:
            return None
    # PEP 610: pip and uv write direct_url.json for path, editable, VCS and URL
    # installs, never for installs from an index.
    raw = dist.read_text("direct_url.json")
    if raw is None:
        return None
    try:
        return json.loads(raw).get("url") or "an unknown location"
    except ValueError:
        return "an unknown location"


def _pinned_requirement() -> str:
    try:
        requirements = metadata.requires("llmdbenchmark") or []
    except metadata.PackageNotFoundError:
        requirements = []
    for requirement in requirements:
        if requirement.startswith(DISTRIBUTION):
            return requirement
    return DISTRIBUTION


def require_published_benchmark_report(
    dist: metadata.Distribution | None = None,
) -> None:
    """Raise ImportError unless llmd-benchmark-report came from a package index."""
    # A copy installed from the checkout can carry fields or checks no release
    # has, so its reports may not validate for consumers pinned to a release.
    if os.environ.get(OVERRIDE_ENV) == "1":
        return
    url = local_install_url(dist)
    if url is None:
        return
    raise ImportError(
        f"{DISTRIBUTION} is installed from {url}, not from PyPI. "
        "llm-d-benchmark only runs against published releases. "
        f'Run `pip install --force-reinstall --no-deps "{_pinned_requirement()}"`, '
        f"or set {OVERRIDE_ENV}=1 to try an unreleased schema locally."
    )
