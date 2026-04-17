"""Tests for util/generate_sbom.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Module loader -- generate_sbom lives under util/, not on sys.path
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SBOM_PATH = _REPO_ROOT / "util" / "generate_sbom.py"


@pytest.fixture(scope="module")
def sbom_module():
    spec = importlib.util.spec_from_file_location("generate_sbom", _SBOM_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_sbom"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


_INSTALL_SH_FIXTURE = """\
#!/bin/bash
# Toy install.sh fragment

tools="curl git helm helmfile yq crane jq"

install_yq_linux() {
    local version=v4.52.5
    curl -sL "https://example/${version}/yq" -o /tmp/yq
}

install_helmfile_linux() {
    local version=1.1.3
    curl -sL "https://example/v${version}/helmfile" -o /tmp/helmfile
}

install_crane_linux() {
    local version=v0.20.3
    curl -sL "https://example/${version}/crane" -o /tmp/crane
}

helm_diff_url="https://github.com/databus23/helm-diff"

PLANNER_GIT="git+https://github.com/llm-d-incubation/llm-d-planner.git@deadbeefcafe"
"""


_DEFAULTS_YAML_FIXTURE = """\
images:
  benchmark:
    repository: ghcr.io/llm-d/llm-d-benchmark
    tag: auto
    sourceRepo: https://github.com/llm-d/llm-d-benchmark
  python:
    repository: python
    tag: "3.10"
    sourceRepo: https://hub.docker.com/_/python
  unknownImage:
    repository: ghcr.io/somewhere/unknown
    tag: v1
    # intentionally no sourceRepo -- exercises the fallback

helmRepositories:
  istio:
    url: https://istio-release.storage.googleapis.com/charts
    sourceRepo: https://github.com/istio/istio
  llmDInfra:
    url: https://llm-d-incubation.github.io/llm-d-infra/
    sourceRepo: https://github.com/llm-d-incubation/llm-d-infra
  inferencePool:
    sourceRepo: https://github.com/kubernetes-sigs/gateway-api-inference-extension

chartVersions:
  istioBase: 1.29.1
  llmDInfra: auto
  inferencePool: v1.3.0
"""


_PYPROJECT_FIXTURE = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = [
    "PyYAML",
    "pydantic>=2.0",
    "Jinja2",
]
"""


@pytest.fixture
def install_sh(tmp_path: Path) -> Path:
    p = tmp_path / "install.sh"
    p.write_text(_INSTALL_SH_FIXTURE, encoding="utf-8")
    return p


@pytest.fixture
def defaults_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "defaults.yaml"
    p.write_text(_DEFAULTS_YAML_FIXTURE, encoding="utf-8")
    return p


@pytest.fixture
def pyproject_file(tmp_path: Path) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(_PYPROJECT_FIXTURE, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# install.sh parser
# --------------------------------------------------------------------------- #


def test_parse_install_sh_pinned_versions(sbom_module, install_sh: Path) -> None:
    entries = sbom_module.parse_install_sh(install_sh)
    by_name = {e.name: e for e in entries}

    assert by_name["yq"].pin == "v4.52.5"
    assert by_name["yq"].pin_type == "version"
    assert "install.sh" in by_name["yq"].location
    assert "install_yq_linux" in by_name["yq"].location
    # Pin is on the `local version=...` line, not the function header.
    assert "line " in by_name["yq"].location

    assert by_name["helmfile"].pin == "1.1.3"
    assert by_name["crane"].pin == "v0.20.3"


def test_parse_install_sh_unpinned_marks_system_provided(
    sbom_module, install_sh: Path,
) -> None:
    entries = sbom_module.parse_install_sh(install_sh)
    by_name = {e.name: e for e in entries}
    assert by_name["jq"].pin == "system-provided"
    assert by_name["jq"].pin_type == "system-provided"
    assert "command -v" in by_name["jq"].location


def test_parse_install_sh_planner_commit(sbom_module, install_sh: Path) -> None:
    entries = sbom_module.parse_install_sh(install_sh)
    planner = next((e for e in entries if e.name == "llm-d-planner (git)"), None)
    assert planner is not None
    assert planner.pin == "deadbeefcafe"
    assert planner.pin_type == "commit SHA"
    assert "PLANNER_GIT" in planner.location


def test_parse_install_sh_helm_diff_recorded(sbom_module, install_sh: Path) -> None:
    entries = sbom_module.parse_install_sh(install_sh)
    by_name = {e.name: e for e in entries}
    assert "helm-diff" in by_name
    assert by_name["helm-diff"].pin == "latest"
    assert "helm_diff_url" in by_name["helm-diff"].location


def test_parse_install_sh_missing_file(sbom_module, tmp_path: Path) -> None:
    assert sbom_module.parse_install_sh(tmp_path / "does-not-exist") == []


def test_parse_install_sh_known_upstream_links(sbom_module, install_sh: Path) -> None:
    entries = sbom_module.parse_install_sh(install_sh)
    by_name = {e.name: e for e in entries}
    # Sanity-check a couple of known upstream mappings.
    assert "github.com/mikefarah/yq" in by_name["yq"].upstream
    assert "github.com/helmfile/helmfile" in by_name["helmfile"].upstream
    assert "github.com/google/go-containerregistry" in by_name["crane"].upstream


# --------------------------------------------------------------------------- #
# pyproject.toml parser (tracks line numbers)
# --------------------------------------------------------------------------- #


def test_parse_pyproject_records_line_numbers(
    sbom_module, pyproject_file: Path,
) -> None:
    entries, line_map = sbom_module.parse_pyproject_dependencies(pyproject_file)
    by_name = {e.name: e for e in entries}
    # PyYAML is at line 5 (1-indexed) in the fixture.
    assert by_name["PyYAML"].pin == "(unpinned)"
    assert by_name["PyYAML"].pin_type == "(unpinned)"
    assert "line " in by_name["PyYAML"].location
    assert by_name["pydantic"].pin == ">=2.0"
    assert by_name["pydantic"].pin_type == "constraint"
    # line_map should be lowercase keys.
    assert line_map["pyyaml"] > 0
    assert line_map["pydantic"] > 0


def test_parse_pyproject_pypi_links(sbom_module, pyproject_file: Path) -> None:
    entries, _ = sbom_module.parse_pyproject_dependencies(pyproject_file)
    by_name = {e.name: e for e in entries}
    # Unknown packages should fall back to PyPI links.
    assert "pypi.org/project/pyyaml" in by_name["PyYAML"].upstream.lower()


# --------------------------------------------------------------------------- #
# Helm chart resolution
# --------------------------------------------------------------------------- #


class _FakeResolver:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def resolve_chart_version(self, chart_name: str, repo_url: str | None = None) -> str:
        return f"resolved-{chart_name}"

    def resolve_image_tag(self, registry: str, repository: str) -> str:
        return f"resolved-tag-{repository.split('/')[-1]}"


class _FailingResolver:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def resolve_chart_version(self, *_args, **_kwargs):
        raise RuntimeError("network unreachable")

    def resolve_image_tag(self, *_args, **_kwargs):
        raise RuntimeError("registry timeout")


def test_helm_charts_resolves_auto(sbom_module, defaults_yaml: Path) -> None:
    charts = sbom_module.collect_helm_charts(
        defaults_yaml,
        resolve=True,
        resolver_factory=lambda _logger: _FakeResolver(),
    )
    by_name = {c.name: c for c in charts}
    assert by_name["istioBase"].pin == "1.29.1"
    assert by_name["istioBase"].pin_type == "tag"
    assert by_name["llmDInfra"].pin == "resolved-llmDInfra"
    assert by_name["llmDInfra"].pin_type == "tag (auto-resolved)"
    assert by_name["inferencePool"].pin == "v1.3.0"
    # File location should reference the chartVersions block.
    assert "chartVersions.istioBase" in by_name["istioBase"].location


def test_helm_charts_no_resolve_marks_skipped(
    sbom_module, defaults_yaml: Path,
) -> None:
    charts = sbom_module.collect_helm_charts(defaults_yaml, resolve=False)
    by_name = {c.name: c for c in charts}
    assert by_name["llmDInfra"].pin == "auto (resolution skipped)"
    assert by_name["llmDInfra"].pin_type == "tag (auto, unresolved)"


def test_helm_charts_resolution_failure_marked(
    sbom_module, defaults_yaml: Path,
) -> None:
    charts = sbom_module.collect_helm_charts(
        defaults_yaml,
        resolve=True,
        resolver_factory=lambda _logger: _FailingResolver(),
    )
    by_name = {c.name: c for c in charts}
    assert by_name["llmDInfra"].pin.startswith("auto (resolution failed:")
    assert "network unreachable" in by_name["llmDInfra"].pin
    assert by_name["llmDInfra"].pin_type == "tag (auto, failed)"


def test_helm_charts_appends_repo_url_to_upstream(
    sbom_module, defaults_yaml: Path,
) -> None:
    charts = sbom_module.collect_helm_charts(
        defaults_yaml,
        resolve=True,
        resolver_factory=lambda _logger: _FakeResolver(),
    )
    by_name = {c.name: c for c in charts}
    # Known chart whose helmRepositories url should be appended.
    assert "https://llm-d-incubation.github.io/llm-d-infra/" in by_name["llmDInfra"].upstream


# --------------------------------------------------------------------------- #
# Container image resolution
# --------------------------------------------------------------------------- #


def test_container_images_resolves_auto(sbom_module, defaults_yaml: Path) -> None:
    images = sbom_module.collect_container_images(
        defaults_yaml,
        resolve=True,
        resolver_factory=lambda _logger: _FakeResolver(),
    )
    by_name = {i.name: i for i in images}
    assert by_name["benchmark"].pin == "resolved-tag-llm-d-benchmark"
    assert by_name["benchmark"].pin_type == "tag (auto-resolved)"
    assert by_name["python"].pin == "3.10"
    assert by_name["python"].pin_type == "tag"
    # Repository should be appended to upstream link.
    assert "ghcr.io/llm-d/llm-d-benchmark" in by_name["benchmark"].upstream


def test_container_images_no_resolve_marks_skipped(
    sbom_module, defaults_yaml: Path,
) -> None:
    images = sbom_module.collect_container_images(defaults_yaml, resolve=False)
    by_name = {i.name: i for i in images}
    assert by_name["benchmark"].pin == "auto (resolution skipped)"
    assert by_name["python"].pin == "3.10"


# --------------------------------------------------------------------------- #
# Block-line parser
# --------------------------------------------------------------------------- #


def test_find_block_child_lines(sbom_module, defaults_yaml: Path) -> None:
    text = defaults_yaml.read_text(encoding="utf-8")
    lines = sbom_module._find_block_child_lines(text, "chartVersions")
    # Verify the parser found all three children with positive line numbers.
    assert set(lines.keys()) == {"istioBase", "llmDInfra", "inferencePool"}
    assert all(v > 0 for v in lines.values())
    # llmDInfra should come after istioBase in the file.
    assert lines["istioBase"] < lines["llmDInfra"] < lines["inferencePool"]


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def test_render_markdown_includes_all_sections(sbom_module) -> None:
    out = sbom_module.render_markdown(
        system_tools=[],
        py_direct=[],
        py_installed=[],
        helm_charts=[],
        container_images=[],
        git_sha="x",
        generated_at_utc="y",
    )
    for heading in (
        "## System Tool Dependencies",
        "## Helm Chart Dependencies",
        "## Container Image Dependencies",
        "## Python Package Dependencies (declared)",
    ):
        assert heading in out
    # New format header should be present.
    assert "# Upstream Dependency Version Tracking" in out


def test_render_markdown_deterministic(sbom_module) -> None:
    args = dict(
        system_tools=[
            sbom_module.Entry("yq", "v4.52.5", "version", "loc-y", "ups-y"),
            sbom_module.Entry("crane", "v0.20.3", "version", "loc-c", "ups-c"),
        ],
        py_direct=[],
        py_installed=[],
        helm_charts=[],
        container_images=[],
        git_sha="abc",
        generated_at_utc="t",
    )
    out1 = sbom_module.render_markdown(**args)
    out2 = sbom_module.render_markdown(**args)
    assert out1 == out2
    # Tables should be sorted alphabetically inside the renderer.
    assert out1.find("**crane**") < out1.find("**yq**")


def test_render_markdown_uses_bold_names_and_backticked_pins(sbom_module) -> None:
    out = sbom_module.render_markdown(
        system_tools=[
            sbom_module.Entry("yq", "v4.52.5", "version", "`install.sh` line 326", "[mikefarah/yq](http://x)"),
        ],
        py_direct=[],
        py_installed=[],
        helm_charts=[],
        container_images=[],
        git_sha="x",
        generated_at_utc="y",
    )
    assert "**yq**" in out
    assert "`v4.52.5`" in out
    assert "[mikefarah/yq]" in out


def test_render_markdown_installed_snapshot_only_when_present(sbom_module) -> None:
    out_empty = sbom_module.render_markdown(
        system_tools=[], py_direct=[], py_installed=[],
        helm_charts=[], container_images=[],
        git_sha="x", generated_at_utc="y",
    )
    assert "(installed snapshot)" not in out_empty

    out_with = sbom_module.render_markdown(
        system_tools=[], py_direct=[],
        py_installed=[
            sbom_module.Entry("requests", "2.33.1", "version", "(transitive in `.venv`)", "x"),
        ],
        helm_charts=[], container_images=[],
        git_sha="x", generated_at_utc="y",
    )
    assert "(installed snapshot)" in out_with


# --------------------------------------------------------------------------- #
# CLI: --check exits non-zero on stale, regenerates the file
# --------------------------------------------------------------------------- #


def _scaffold_repo(tmp_path: Path) -> Path:
    repo = tmp_path
    (repo / "install.sh").write_text(_INSTALL_SH_FIXTURE, encoding="utf-8")
    (repo / "pyproject.toml").write_text(_PYPROJECT_FIXTURE, encoding="utf-8")
    cfg_dir = repo / "config" / "templates" / "values"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "defaults.yaml").write_text(_DEFAULTS_YAML_FIXTURE, encoding="utf-8")
    return repo


def test_check_mode_returns_nonzero_on_mismatch(
    sbom_module, tmp_path: Path,
) -> None:
    repo = _scaffold_repo(tmp_path)
    out_path = repo / "SBOM.md"
    out_path.write_text("stale content\n", encoding="utf-8")

    rc = sbom_module.main([
        "--check", "--no-resolve",
        "--repo-root", str(repo),
        "--output", str(out_path),
    ])
    assert rc == 1
    assert "Upstream Dependency" in out_path.read_text(encoding="utf-8")

    rc2 = sbom_module.main([
        "--check", "--no-resolve",
        "--repo-root", str(repo),
        "--output", str(out_path),
    ])
    assert rc2 == 0


def test_check_mode_ignores_volatile_lines(sbom_module, tmp_path: Path) -> None:
    repo = _scaffold_repo(tmp_path)
    out = repo / "SBOM.md"
    rc = sbom_module.main([
        "--no-resolve", "--repo-root", str(repo), "--output", str(out),
    ])
    assert rc == 0
    first = out.read_text(encoding="utf-8")
    altered = first.replace("Generated at: ", "Generated at: 1970-01-01 00:00:00 ")
    out.write_text(altered, encoding="utf-8")

    rc2 = sbom_module.main([
        "--check", "--no-resolve",
        "--repo-root", str(repo), "--output", str(out),
    ])
    assert rc2 == 0


def test_format_source_repo_github_label(sbom_module) -> None:
    out = sbom_module.format_source_repo("https://github.com/llm-d/llm-d-kv-cache")
    assert out == "[llm-d/llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache)"


def test_format_source_repo_with_path(sbom_module) -> None:
    out = sbom_module.format_source_repo(
        "https://github.com/llm-d/llm-d-kv-cache",
        source_path="services/uds_tokenizer",
    )
    assert "[llm-d/llm-d-kv-cache (services/uds_tokenizer)]" in out


def test_format_source_repo_docker_hub(sbom_module) -> None:
    out = sbom_module.format_source_repo("https://hub.docker.com/_/python")
    assert "Docker Hub: python" in out


def test_format_source_repo_empty_returns_unknown(sbom_module) -> None:
    assert sbom_module.format_source_repo(None) == "(unknown)"
    assert sbom_module.format_source_repo("") == "(unknown)"


def test_upstream_for_system_tool(sbom_module) -> None:
    out = sbom_module.upstream_for_system_tool("yq")
    assert "github.com/mikefarah/yq" in out
    # Unknown tool returns "(unknown)"
    assert sbom_module.upstream_for_system_tool("nonsense") == "(unknown)"


def test_upstream_for_python_pkg_uses_pypi(sbom_module) -> None:
    out = sbom_module.upstream_for_python_pkg("Some_Pkg.Name")
    assert "pypi.org/project/some-pkg-name" in out.lower()


def test_helm_charts_unknown_source_repo_marks_unknown(
    sbom_module, defaults_yaml: Path,
) -> None:
    # inferencePool in the fixture has sourceRepo set; remove it on the fly to
    # exercise the unknown path.
    text = defaults_yaml.read_text(encoding="utf-8")
    text = text.replace(
        "  inferencePool:\n    sourceRepo: https://github.com/kubernetes-sigs/gateway-api-inference-extension\n",
        "",
    )
    defaults_yaml.write_text(text, encoding="utf-8")
    charts = sbom_module.collect_helm_charts(defaults_yaml, resolve=False)
    by_name = {c.name: c for c in charts}
    assert by_name["inferencePool"].upstream == "(unknown)"


def test_container_images_reads_source_path(
    sbom_module, defaults_yaml: Path,
) -> None:
    """The optional sourcePath should appear in the rendered upstream link."""
    text = defaults_yaml.read_text(encoding="utf-8")
    text = text.replace(
        "    sourceRepo: https://github.com/llm-d/llm-d-benchmark",
        "    sourceRepo: https://github.com/llm-d/llm-d-benchmark\n    sourcePath: src",
    )
    defaults_yaml.write_text(text, encoding="utf-8")
    images = sbom_module.collect_container_images(defaults_yaml, resolve=False)
    by_name = {i.name: i for i in images}
    assert "(src)" in by_name["benchmark"].upstream


def test_container_images_unknown_source_repo_marks_unknown(
    sbom_module, defaults_yaml: Path,
) -> None:
    images = sbom_module.collect_container_images(defaults_yaml, resolve=False)
    by_name = {i.name: i for i in images}
    # `unknownImage` in the fixture has no sourceRepo
    assert by_name["unknownImage"].upstream == "(unknown)"
