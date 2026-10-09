"""llm-d-benchmark consumes llmd-benchmark-report only as a published release."""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from llmdbenchmark import _report_source

PROJECT_ROOT = Path(__file__).resolve().parent.parent
THIS_FILE = Path(__file__).resolve().relative_to(PROJECT_ROOT).as_posix()
LOCAL_INSTALL = (
    '{"url": "file:///repo/benchmark-report", "dir_info": {"editable": true}}'
)

# The only files allowed to install the checkout, and how many times. Every
# other file in the repo must take the package from PyPI.
CHECKOUT_INSTALLS_ALLOWED = {
    # Builds the release wheel and smoke-tests it before upload.
    ".github/workflows/br-release.yaml": 1,
    # The Benchmark Report Tests job, the CI opt-in to the checkout.
    ".github/workflows/ci-pr-benchmark.yaml": 1,
    # Documents the opt-in for local schema work.
    "benchmark-report/README.md": 1,
}
CHECKOUT_INSTALL = re.compile(
    r"^\s*(ADD|COPY)\b.*(?<!llmd-)benchmark-report|\binstall\b.*(?<!llmd-)benchmark-report",
    re.MULTILINE,
)


# Stands in for importlib.metadata.Distribution: returns the given
# direct_url.json text, or None as an index install does.
class _FakeDist:
    def __init__(self, direct_url):
        self._direct_url = direct_url

    def read_text(self, name):
        return self._direct_url if name == "direct_url.json" else None


# Pulls the exact version out of "llmd-benchmark-report==X" in a list of
# requirement strings. ["pyyaml", "llmd-benchmark-report==0.2.1"] -> "0.2.1".
def _pinned_version(requirements):
    pins = [
        r.split("==", 1)[1].strip()
        for r in requirements
        if r.strip().startswith("llmd-benchmark-report==")
    ]
    assert len(pins) == 1, f"expected one exact llmd-benchmark-report pin, got {pins}"
    return pins[0]


# An index install records no direct_url.json. Expects no source URL.
def test_index_install_has_no_source_url():
    assert _report_source.local_install_url(_FakeDist(None)) is None


# An editable install from /repo/benchmark-report records that path.
# Expects "file:///repo/benchmark-report" back.
def test_path_install_reports_its_source():
    dist = _FakeDist(LOCAL_INSTALL)
    assert _report_source.local_install_url(dist) == "file:///repo/benchmark-report"


# The editable install with the override unset. Expects an ImportError that
# names the install path and the override variable.
def test_guard_rejects_path_install(monkeypatch):
    monkeypatch.delenv(_report_source.OVERRIDE_ENV, raising=False)
    with pytest.raises(ImportError, match="file:///repo/benchmark-report") as err:
        _report_source.require_published_benchmark_report(_FakeDist(LOCAL_INSTALL))
    assert _report_source.OVERRIDE_ENV in str(err.value)


# The editable install with LLMDBENCH_ALLOW_LOCAL_BENCHMARK_REPORT=1.
# Expects no error.
def test_guard_allows_override(monkeypatch):
    monkeypatch.setenv(_report_source.OVERRIDE_ENV, "1")
    _report_source.require_published_benchmark_report(_FakeDist(LOCAL_INSTALL))


# An index install with the override unset. Expects no error.
def test_guard_accepts_index_install(monkeypatch):
    monkeypatch.delenv(_report_source.OVERRIDE_ENV, raising=False)
    _report_source.require_published_benchmark_report(_FakeDist(None))


# pyproject.toml, build/requirements-analysis.txt and
# llm_d_stack_discovery/requirements.txt each pin one exact release.
# Expects all three to name the same version, e.g. 0.2.1.
def test_pins_agree():
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        pyproject = _pinned_version(tomllib.load(f)["project"]["dependencies"])
    analysis = _pinned_version(
        (PROJECT_ROOT / "build/requirements-analysis.txt").read_text().splitlines()
    )
    discovery = _pinned_version(
        (PROJECT_ROOT / "llm_d_stack_discovery/requirements.txt")
        .read_text()
        .splitlines()
    )
    assert pyproject == analysis == discovery


# Every tracked or new unignored file but this one. Expects no line that
# copies or pip-installs the in-repo benchmark-report/ directory beyond
# CHECKOUT_INSTALLS_ALLOWED, e.g. a new Dockerfile running
# "pip install ./benchmark-report" fails.
def test_nothing_installs_the_checkout():
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.split("\0")
    except OSError, subprocess.CalledProcessError:
        pytest.skip("needs a git checkout to list tracked files")
    offending = {}
    for name in tracked:
        if not name or name == THIS_FILE:
            continue
        try:
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        hits = [m.group(0).strip() for m in CHECKOUT_INSTALL.finditer(text)]
        if len(hits) > CHECKOUT_INSTALLS_ALLOWED.get(name, 0):
            offending[name] = hits
    assert not offending, f"install the in-repo package: {offending}"
