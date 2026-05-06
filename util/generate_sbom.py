#!/usr/bin/env python3
"""Generate the Upstream Dependency Version Tracking file (SBOM.md).

Consolidates four sources of versioned dependencies into a single
markdown file at the repo root:

  1. System tools     (parsed from ``install.sh``, with line numbers)
  2. Python packages  (from ``pyproject.toml`` for direct deps + git
                       pins from ``install.sh``; full installed set
                       from ``pip freeze`` of the active venv)
  3. Helm charts      (from ``config/templates/values/defaults.yaml``,
                       with ``auto`` resolved via the existing
                       ``VersionResolver``)
  4. Container images (from ``config/templates/values/defaults.yaml``,
                       with ``auto`` resolved via the existing
                       ``VersionResolver``)

Each entry tracks: dependency name, current pin, pin type
(commit SHA / version / tag / constraint / etc.), file location with
line number, and a markdown link to the upstream repo.

Run modes:

  python util/generate_sbom.py                 # write SBOM.md
  python util/generate_sbom.py --no-resolve    # skip network, mark auto
  python util/generate_sbom.py --check         # exit non-zero if stale

The ``--check`` form is what the precommit hook invokes: it regenerates
SBOM.md, fails the commit if the file changed, and prints a message
asking the user to ``git add`` and re-commit.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# The VersionResolver lives inside the llmdbenchmark package; make sure
# the repo root is on the path so ``import llmdbenchmark...`` works
# regardless of the cwd the script is invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# --------------------------------------------------------------------------- #
# Upstream repository mapping
# --------------------------------------------------------------------------- #
#
# Each known dependency name maps to a markdown link that points at the
# canonical source repository. Unknown PyPI packages get a
# ``pypi.org/project/<name>/`` link generated on the fly. Unknown anything
# else falls back to ``(unknown)`` so reviewers can spot gaps.
#
# Keep this dict alphabetised within sections so diffs stay clean.

# Source-of-truth for *system tools* upstream repos. These tools are
# referenced in install.sh (not in defaults.yaml), so they need a static
# mapping here. Helm charts and container images get their source repo
# from `defaults.yaml`'s ``sourceRepo`` keys -- see ``format_source_repo``.
SYSTEM_TOOL_REPOS: dict[str, str] = {
    "crane": "https://github.com/google/go-containerregistry",
    "curl": "https://github.com/curl/curl",
    "git": "https://github.com/git/git",
    "helm": "https://github.com/helm/helm",
    "helm-diff": "https://github.com/databus23/helm-diff",
    "helmfile": "https://github.com/helmfile/helmfile",
    "jq": "https://github.com/jqlang/jq",
    "kubectl": "https://github.com/kubernetes/kubernetes",
    "kustomize": "https://github.com/kubernetes-sigs/kustomize",
    "llm-d-planner (git)": "https://github.com/llm-d-incubation/llm-d-planner",
    "oc": "https://github.com/openshift/oc",
    "skopeo": "https://github.com/containers/skopeo",
    "yq": "https://github.com/mikefarah/yq",
}


def format_source_repo(url: str | None, *, source_path: str | None = None) -> str:
    """Format a source-repo URL as ``[org/repo (subpath)](url)`` markdown.

    The link text is derived from the URL (last two path segments for github
    URLs, ``Docker Hub: <name>`` for hub.docker.com). When *source_path* is
    given, it's appended in parentheses so reviewers can see exactly where in
    the repo the artifact lives. Returns ``"(unknown)"`` when *url* is empty.
    """
    if not url:
        return "(unknown)"
    label = _label_from_url(url)
    if source_path:
        label = f"{label} ({source_path})"
    return f"[{label}]({url})"


def _label_from_url(url: str) -> str:
    """Build a short link-text label from a repo URL."""
    if "hub.docker.com" in url:
        # https://hub.docker.com/_/python -> "Docker Hub: python"
        name = url.rstrip("/").rsplit("/", 1)[-1]
        return f"Docker Hub: {name}"
    # GitHub-style: take the last two path segments (org/repo)
    parts = re.sub(r"^https?://[^/]+/", "", url.rstrip("/")).split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return url


def upstream_for_system_tool(name: str) -> str:
    """Markdown link for a system tool listed in ``SYSTEM_TOOL_REPOS``."""
    return format_source_repo(SYSTEM_TOOL_REPOS.get(name))


def upstream_for_python_pkg(name: str) -> str:
    """PyPI fallback for declared Python packages."""
    slug = re.sub(r"[._]+", "-", name).lower()
    return f"[{name} (PyPI)](https://pypi.org/project/{slug}/)"


# --------------------------------------------------------------------------- #
# Data containers (each row in an SBOM table)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Entry:
    """A single SBOM row -- shared shape across all four tables."""
    name: str
    pin: str
    pin_type: str
    location: str       # e.g. "`install.sh` line 326 (`install_yq_linux`)"
    upstream: str       # markdown link or "(unknown)"


# --------------------------------------------------------------------------- #
# Logger shim for VersionResolver
# --------------------------------------------------------------------------- #


class _StdLogger:
    """Minimal logger interface that VersionResolver expects."""

    def __init__(self, verbose: bool = False) -> None:
        self._log = logging.getLogger("sbom")
        if not self._log.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._log.addHandler(handler)
        self._log.setLevel(logging.INFO if verbose else logging.WARNING)

    def log_info(self, msg: str) -> None: self._log.info(msg)
    def log_warning(self, msg: str) -> None: self._log.warning(msg)
    def log_error(self, msg: str) -> None: self._log.error(msg)
    def log_debug(self, msg: str) -> None: self._log.debug(msg)


# --------------------------------------------------------------------------- #
# install.sh parser (now tracks line numbers)
# --------------------------------------------------------------------------- #

_VERSION_LINE_RE = re.compile(
    r"^\s*(?:local\s+)?version=(?P<v>[\"']?)(?P<val>[^\s\"']+)(?P=v)\s*$"
)
_INSTALL_FN_RE = re.compile(
    r"^\s*install_(?P<tool>[a-zA-Z0-9_-]+?)_(?:linux|mac)\s*\(\s*\)\s*\{?\s*$"
)
_TOOLS_VAR_RE = re.compile(
    r"^\s*tools=(?P<q>[\"'])(?P<list>[^\"']+)(?P=q)\s*$"
)
_PLANNER_RE = re.compile(
    r'^\s*PLANNER_GIT="(?P<url>git\+https://[^"]+)"\s*$'
)
_HELM_DIFF_RE = re.compile(
    r"^\s*helm_diff_url=[\"']?(https://[^\s\"']+)"
)


def parse_install_sh(install_sh_path: Path) -> list[Entry]:
    """Extract pinned tool versions and the required-tool list from install.sh.

    Tracks 1-based line numbers so file_location includes them.
    """
    if not install_sh_path.exists():
        return []

    text = install_sh_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # tool_name -> (version, line_number, fn_name) for pinned installs
    pinned: dict[str, tuple[str, int, str]] = {}
    tools_seen: set[str] = set()
    planner_url: str | None = None
    planner_line: int | None = None
    helm_diff_line: int | None = None

    current_fn: str | None = None
    current_fn_line: int = 0
    rel = install_sh_path.name

    for i, raw in enumerate(lines, start=1):
        m = _INSTALL_FN_RE.match(raw)
        if m:
            current_fn = m.group("tool")
            current_fn_line = i
            continue

        if current_fn:
            m = _VERSION_LINE_RE.match(raw)
            if m and current_fn not in pinned:
                pinned[current_fn] = (m.group("val"), i, f"install_{current_fn}_linux")
                continue

        m = _TOOLS_VAR_RE.match(raw)
        if m:
            for token in m.group("list").split():
                if token.startswith("$"):
                    continue
                tools_seen.add(token)
            continue

        m = _PLANNER_RE.match(raw)
        if m:
            planner_url = m.group("url")
            planner_line = i
            continue

        m = _HELM_DIFF_RE.match(raw)
        if m and helm_diff_line is None:
            helm_diff_line = i
            continue

    # Build entries.
    out: list[Entry] = []
    for tool in sorted(tools_seen):
        if tool in pinned:
            version, line_num, fn_name = pinned[tool]
            out.append(Entry(
                name=tool,
                pin=version,
                pin_type="version",
                location=f"`{rel}` line {line_num} (`{fn_name}`)",
                upstream=upstream_for_system_tool(tool),
            ))
        else:
            # Tool is required but its version is whatever the host provides.
            out.append(Entry(
                name=tool,
                pin="system-provided",
                pin_type="system-provided",
                location=f"`{rel}`: `command -v` check (no pin)",
                upstream=upstream_for_system_tool(tool),
            ))

    # helm-diff plugin is unconditionally installed.
    out.append(Entry(
        name="helm-diff",
        pin="latest",
        pin_type="plugin (latest)",
        location=(
            f"`{rel}` line {helm_diff_line} (`helm_diff_url`)"
            if helm_diff_line is not None
            else f"`{rel}`: `helm plugin install`"
        ),
        upstream=upstream_for_system_tool("helm-diff"),
    ))

    # Planner git pin (record under system tools because it's pinned in install.sh).
    if planner_url:
        sha = planner_url.split("@")[-1] if "@" in planner_url else "(no sha)"
        out.append(Entry(
            name="llm-d-planner (git)",
            pin=sha,
            pin_type="commit SHA",
            location=(
                f"`{rel}` line {planner_line} (`PLANNER_GIT`)"
                if planner_line is not None
                else f"`{rel}` (`PLANNER_GIT`)"
            ),
            upstream=upstream_for_system_tool("llm-d-planner (git)"),
        ))

    return sorted(out, key=lambda t: t.name.lower())


# --------------------------------------------------------------------------- #
# pyproject.toml dependency parser (tracks line numbers)
# --------------------------------------------------------------------------- #


def parse_pyproject_dependencies(
    pyproject_path: Path,
) -> tuple[list[Entry], dict[str, int]]:
    """Return (entries from pyproject.toml, name -> line-number map).

    The line map is also used to enrich pip-freeze entries with the
    pyproject location when a transitive dep is actually a direct one.
    """
    if not pyproject_path.exists():
        return [], {}

    text = pyproject_path.read_text(encoding="utf-8", errors="replace")
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        data = tomllib.loads(text)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"warning: could not parse pyproject.toml: {exc}\n")
        return [], {}

    deps_declared = data.get("project", {}).get("dependencies", []) or []

    # Find the line numbers of each dep inside `dependencies = [ ... ]`.
    # We scan the raw text because tomllib doesn't expose source positions.
    lines = text.splitlines()
    line_map: dict[str, int] = {}
    in_deps = False
    for i, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("dependencies"):
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("]"):
                break
            m = re.match(r'^\s*[\'"]([A-Za-z0-9_.\-]+)([^\'"]*)[\'"]', raw)
            if m:
                line_map[m.group(1).lower()] = i

    rel = pyproject_path.name
    entries: list[Entry] = []
    for raw in deps_declared:
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*(.*)$", raw)
        if not m:
            continue
        name, spec = m.group(1), m.group(2).strip()
        line_num = line_map.get(name.lower())
        loc = f"`{rel}` line {line_num}" if line_num else f"`{rel}` (`dependencies`)"
        entries.append(Entry(
            name=name,
            pin=spec or "(unpinned)",
            pin_type="constraint" if spec else "(unpinned)",
            location=loc,
            upstream=upstream_for_python_pkg(name),
        ))
    return sorted(entries, key=lambda e: e.name.lower()), line_map


# --------------------------------------------------------------------------- #
# pip freeze (transitive snapshot)
# --------------------------------------------------------------------------- #


def collect_pip_freeze(
    venv_python: Path | None,
    direct_line_map: dict[str, int],
    pyproject_path: Path,
) -> list[Entry]:
    """Run pip freeze against the venv and return all installed packages.

    Direct deps that also appear in pyproject get the pyproject line in
    their location field; transitive deps say ``(transitive in .venv)``.
    """
    if not venv_python or not venv_python.exists():
        return []
    try:
        result = subprocess.run(
            [str(venv_python), "-m", "pip", "freeze"],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"warning: pip freeze failed ({exc})\n")
        return []

    pyproj_rel = pyproject_path.name
    entries: list[Entry] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        name, pin, pin_type = "", "", ""
        if line.startswith("-e ") or line.startswith("--editable"):
            if "#egg=" in line:
                name = line.split("#egg=", 1)[1].split("&", 1)[0]
            else:
                name = line.rsplit("/", 1)[-1].split(".", 1)[0]
            pin, pin_type = "editable", "editable"
        elif " @ " in line:
            head, _, source = line.partition(" @ ")
            name = head.strip()
            sha = source.split("@")[-1] if "@" in source else "(unknown)"
            pin, pin_type = sha, "commit SHA"
        elif "==" in line:
            name, _, ver = line.partition("==")
            name, pin, pin_type = name.strip(), ver.strip(), "version"
        else:
            name = line
            pin, pin_type = "(unparsed)", "(unparsed)"

        line_num = direct_line_map.get(name.lower())
        if line_num is not None:
            loc = f"`{pyproj_rel}` line {line_num} (direct)"
        else:
            loc = "(transitive in `.venv`)"
        entries.append(Entry(
            name=name,
            pin=pin,
            pin_type=pin_type,
            location=loc,
            upstream=upstream_for_python_pkg(name),
        ))
    return sorted(entries, key=lambda e: e.name.lower())


# --------------------------------------------------------------------------- #
# defaults.yaml -- find line numbers for top-level block children
# --------------------------------------------------------------------------- #


def _find_block_child_lines(
    yaml_text: str, top_key: str,
) -> dict[str, int]:
    """Map immediate child keys of ``top_key:`` to their 1-based line numbers."""
    out: dict[str, int] = {}
    lines = yaml_text.splitlines()
    in_block = False
    block_indent: int | None = None
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent == 0 and stripped.rstrip(":").strip() == top_key and stripped.endswith(":"):
            in_block = True
            block_indent = None
            continue
        if not in_block:
            continue
        if indent == 0:
            # Hit the next top-level key -- end of block.
            break
        if block_indent is None:
            block_indent = indent
        if indent == block_indent and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            if key not in out:
                out[key] = i
    return out


def _load_defaults(defaults_path: Path) -> dict[str, Any]:
    return yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}


# --------------------------------------------------------------------------- #
# Helm charts + container images
# --------------------------------------------------------------------------- #


def collect_helm_charts(
    defaults_path: Path,
    *,
    resolve: bool,
    resolver_factory: Any = None,
) -> list[Entry]:
    """Read chartVersions/helmRepositories and resolve any ``auto``."""
    defaults = _load_defaults(defaults_path)
    text = defaults_path.read_text(encoding="utf-8")
    chart_versions = defaults.get("chartVersions", {}) or {}
    helm_repos = defaults.get("helmRepositories", {}) or {}
    line_map = _find_block_child_lines(text, "chartVersions")
    rel = _safe_relpath(defaults_path)

    resolver = None
    if resolve and any(v == "auto" for v in chart_versions.values()):
        resolver = _make_resolver(resolver_factory)

    out: list[Entry] = []
    for chart_key, version in chart_versions.items():
        repo_info = helm_repos.get(chart_key, {}) or {}
        repo_name = repo_info.get("name", chart_key)
        repo_url = repo_info.get("url", "")

        resolved = str(version)
        pin_type = "tag"
        if version == "auto":
            if resolver is None:
                resolved = "auto (resolution skipped)"
                pin_type = "tag (auto, unresolved)"
            else:
                try:
                    resolved = resolver.resolve_chart_version(repo_name, repo_url=repo_url)
                    pin_type = "tag (auto-resolved)"
                except Exception as exc:
                    resolved = f"auto (resolution failed: {_short_err(exc)})"
                    pin_type = "tag (auto, failed)"

        line_num = line_map.get(chart_key)
        loc = (
            f"`{rel}` line {line_num} (`chartVersions.{chart_key}`)"
            if line_num is not None
            else f"`{rel}` (`chartVersions.{chart_key}`)"
        )
        # Read the source repo straight from defaults.yaml's
        # helmRepositories.<key>.sourceRepo (single source of truth).
        # Append the actual chart pull URL so reviewers can trace both.
        upstream = format_source_repo(repo_info.get("sourceRepo"))
        if repo_url and upstream != "(unknown)":
            upstream = f"{upstream} (`{repo_url}`)"

        out.append(Entry(
            name=chart_key,
            pin=resolved,
            pin_type=pin_type,
            location=loc,
            upstream=upstream,
        ))
    return sorted(out, key=lambda e: e.name.lower())


def collect_container_images(
    defaults_path: Path,
    *,
    resolve: bool,
    resolver_factory: Any = None,
) -> list[Entry]:
    """Read images and resolve any ``tag: auto``."""
    defaults = _load_defaults(defaults_path)
    text = defaults_path.read_text(encoding="utf-8")
    images = defaults.get("images", {}) or {}
    line_map = _find_block_child_lines(text, "images")
    rel = _safe_relpath(defaults_path)

    resolver = None
    if resolve and any(
        isinstance(v, dict) and v.get("tag") == "auto" for v in images.values()
    ):
        resolver = _make_resolver(resolver_factory)

    out: list[Entry] = []
    for image_key, image_config in images.items():
        if not isinstance(image_config, dict):
            continue
        repo = image_config.get("repository", "")
        tag = image_config.get("tag", "")
        resolved_tag = str(tag)
        pin_type = "tag"
        if tag == "auto":
            if resolver is None:
                resolved_tag = "auto (resolution skipped)"
                pin_type = "tag (auto, unresolved)"
            else:
                try:
                    resolved_tag = resolver.resolve_image_tag("", repo)
                    pin_type = "tag (auto-resolved)"
                except Exception as exc:
                    resolved_tag = f"auto (resolution failed: {_short_err(exc)})"
                    pin_type = "tag (auto, failed)"

        line_num = line_map.get(image_key)
        loc = (
            f"`{rel}` line {line_num} (`images.{image_key}`)"
            if line_num is not None
            else f"`{rel}` (`images.{image_key}`)"
        )
        # Read the source repo straight from defaults.yaml (single source of
        # truth). The optional `sourcePath` says where in the repo the image
        # is built from (e.g. a subdir or specific Dockerfile).
        upstream = format_source_repo(
            image_config.get("sourceRepo"),
            source_path=image_config.get("sourcePath"),
        )
        if repo and upstream != "(unknown)":
            upstream = f"{upstream} (`{repo}`)"

        out.append(Entry(
            name=image_key,
            pin=resolved_tag,
            pin_type=pin_type,
            location=loc,
            upstream=upstream,
        ))
    return sorted(out, key=lambda e: e.name.lower())


def _safe_relpath(p: Path) -> Path:
    """Return a path relative to the repo root if possible; else just the basename.

    Tests scaffold fixtures under /tmp, which is outside the repo, so a strict
    ``relative_to`` would raise. Falling back to the file name keeps the SBOM
    rendering portable across test runs.
    """
    try:
        return p.relative_to(_REPO_ROOT)
    except ValueError:
        return Path(p.name)


def _make_resolver(resolver_factory: Any):
    if resolver_factory is not None:
        return resolver_factory(_StdLogger())
    from llmdbenchmark.parser.version_resolver import VersionResolver
    return VersionResolver(logger=_StdLogger())


def _short_err(exc: BaseException) -> str:
    msg = str(exc).strip().splitlines()[0] if str(exc) else type(exc).__name__
    return (msg[:120] + "...") if len(msg) > 120 else msg


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def _md_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return (
            "| " + " | ".join(headers) + " |\n"
            "|" + "|".join(["---"] * len(headers)) + "|\n"
            "| _(no entries)_ |" + (" |" * (len(headers) - 1)) + "\n"
        )
    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        cells = [str(c).replace("|", "\\|") for c in row]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def _entries_to_rows(entries: list[Entry]) -> list[tuple[str, ...]]:
    return [
        (f"**{e.name}**", f"`{e.pin}`", e.pin_type, e.location, e.upstream)
        for e in entries
    ]


def render_markdown(
    *,
    system_tools: list[Entry],
    py_direct: list[Entry],
    py_installed: list[Entry],
    helm_charts: list[Entry],
    container_images: list[Entry],
    git_sha: str,
    generated_at_utc: str,
) -> str:
    """Render the SBOM markdown in the upstream-tracking style."""
    # Defensive sort.
    system_tools = sorted(system_tools, key=lambda e: e.name.lower())
    py_direct = sorted(py_direct, key=lambda e: e.name.lower())
    py_installed = sorted(py_installed, key=lambda e: e.name.lower())
    helm_charts = sorted(helm_charts, key=lambda e: e.name.lower())
    container_images = sorted(container_images, key=lambda e: e.name.lower())

    headers = ("Dependency", "Current Pin", "Pin Type", "File Location", "Upstream Repo")

    parts: list[str] = []
    parts.append("# Upstream Dependency Version Tracking\n")
    parts.append(
        "> This file is auto-generated by `util/generate_sbom.py` and is the source\n"
        "> of truth for tracking external dependencies pinned in this repository,\n"
        "> their current versions, and where they are pinned. The `generate-sbom`\n"
        "> precommit hook regenerates it whenever `install.sh`, `pyproject.toml`,\n"
        "> `config/templates/values/defaults.yaml`, or the generator script changes.\n"
        "> `auto` Helm/image versions are resolved against live registries at\n"
        "> generation time via the existing `VersionResolver`.\n"
    )
    parts.append(f"- Generated at: `{generated_at_utc}` (UTC)")
    parts.append(f"- Generated against git ref: `{git_sha}`\n")

    parts.append("## System Tool Dependencies\n")
    parts.append(
        "Pinned in `install.sh` install helpers (`install_<tool>_linux`).\n"
        "Tools without an explicit pin are checked via `command -v` and use\n"
        "whatever the host's package manager provides.\n"
    )
    parts.append(_md_table(headers, _entries_to_rows(system_tools)))

    parts.append("\n## Helm Chart Dependencies\n")
    parts.append(
        "Pinned in `config/templates/values/defaults.yaml` `chartVersions:`\n"
        "block. Charts marked `auto` are resolved against the upstream Helm /\n"
        "OCI registry at generation (and plan) time.\n"
    )
    parts.append(_md_table(headers, _entries_to_rows(helm_charts)))

    parts.append("\n## Container Image Dependencies\n")
    parts.append(
        "Pinned in `config/templates/values/defaults.yaml` `images:` block.\n"
        "Tags marked `auto` are resolved against the upstream registry at\n"
        "generation (and plan) time.\n"
    )
    parts.append(_md_table(headers, _entries_to_rows(container_images)))

    parts.append("\n## Python Package Dependencies (declared)\n")
    parts.append(
        "Direct dependencies declared in `pyproject.toml`. The full installed\n"
        "set (including transitive packages picked up by `pip install -e .`) is\n"
        "captured in the snapshot table below.\n"
    )
    parts.append(_md_table(headers, _entries_to_rows(py_direct)))

    if py_installed:
        parts.append("\n## Python Package Dependencies (installed snapshot)\n")
        parts.append(
            "Output of `pip freeze` against the project venv (`.venv`). Includes\n"
            "every transitive dependency actually installed; direct deps are\n"
            "annotated with their `pyproject.toml` line.\n"
        )
        parts.append("<details>")
        parts.append("<summary>Click to expand the full pip-freeze snapshot</summary>\n")
        parts.append(_md_table(headers, _entries_to_rows(py_installed)))
        parts.append("</details>")

    return "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _git_sha(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True,
            check=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "(not a git checkout)"


# --------------------------------------------------------------------------- #
# CLI entrypoint
# --------------------------------------------------------------------------- #


def build_sbom(
    repo_root: Path,
    *,
    resolve: bool,
    resolver_factory: Any = None,
    venv_python: Path | None = None,
    timestamp: str | None = None,
) -> str:
    install_sh = repo_root / "install.sh"
    pyproject = repo_root / "pyproject.toml"
    defaults = repo_root / "config" / "templates" / "values" / "defaults.yaml"
    if venv_python is None:
        venv_python = repo_root / ".venv" / "bin" / "python"

    system_tools = parse_install_sh(install_sh)
    py_direct, direct_line_map = parse_pyproject_dependencies(pyproject)
    py_installed = collect_pip_freeze(venv_python, direct_line_map, pyproject)
    helm_charts = collect_helm_charts(
        defaults, resolve=resolve, resolver_factory=resolver_factory,
    )
    container_images = collect_container_images(
        defaults, resolve=resolve, resolver_factory=resolver_factory,
    )

    generated_at = (
        timestamp
        if timestamp is not None
        else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    )
    return render_markdown(
        system_tools=system_tools,
        py_direct=py_direct,
        py_installed=py_installed,
        helm_charts=helm_charts,
        container_images=container_images,
        git_sha=_git_sha(repo_root),
        generated_at_utc=generated_at,
    )


def _strip_volatile(content: str) -> str:
    """Drop the timestamp + git-sha header lines so --check ignores them."""
    return "\n".join(
        line for line in content.splitlines()
        if not line.startswith("- Generated at:")
        and not line.startswith("- Generated against git ref:")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--output", type=Path, default=_REPO_ROOT / "docs" / "upstream-versions.md",
        help="Where to write the SBOM (default: SBOM.md at repo root).",
    )
    parser.add_argument(
        "--no-resolve", action="store_true",
        help="Skip network calls. Mark `auto` entries as 'resolution skipped'.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Regenerate; exit non-zero if the file changed (precommit mode).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=_REPO_ROOT,
        help="Repository root (default: parent of util/).",
    )
    args = parser.parse_args(argv)

    new_content = build_sbom(args.repo_root, resolve=not args.no_resolve)

    if args.check:
        existing = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if _strip_volatile(existing) == _strip_volatile(new_content):
            return 0
        args.output.write_text(new_content, encoding="utf-8")
        sys.stderr.write(
            f"\nSBOM out of date. Regenerated {args.output.name}.\n"
            f"Please `git add {args.output.relative_to(args.repo_root)}` "
            "and re-commit.\n\n"
        )
        return 1

    args.output.write_text(new_content, encoding="utf-8")
    sys.stderr.write(f"Wrote {args.output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
