"""llm-d-benchmark consumes llmd-benchmark-report only as a published release."""

import re
import tomllib
from pathlib import Path

import pytest

from llmdbenchmark import _report_source

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_INSTALL = (
    '{"url": "file:///repo/benchmark-report", "dir_info": {"editable": true}}'
)

# Images and the installer must take the package from PyPI, never the checkout.
INSTALL_SITES = [
    "build/Dockerfile",
    "build/Dockerfile.s390x",
    "llm_d_stack_discovery/Dockerfile",
    "install.sh",
]
CHECKOUT_INSTALL = re.compile(
    r"^\s*(ADD|COPY)\b.*(?<!llmd-)benchmark-report|\binstall\b.*(?<!llmd-)benchmark-report",
    re.MULTILINE,
)

# Harness tests that import the package only to build or read reports. They
# test harness code, so they stay on the pinned release.
HARNESS_TESTS_USING_PACKAGE = {
    "test_aggregate_eval_containers.py",
    "test_treatment_groups.py",
}
PACKAGE_IMPORT = re.compile(
    r"^\s*(from|import)\s+(llmd_benchmark_report|llmdbenchmark\.analysis\.benchmark_report)\b"
    r"|^\s*from\s+llmdbenchmark\.analysis\s+import\s.*\bbenchmark_report\b",
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


# Each Dockerfile and install.sh. Expects no line that copies or pip-installs
# the in-repo benchmark-report/ directory.
@pytest.mark.parametrize("site", INSTALL_SITES)
def test_nothing_installs_the_checkout(site):
    text = (PROJECT_ROOT / site).read_text()
    offending = [m.group(0).strip() for m in CHECKOUT_INSTALL.finditer(text)]
    assert not offending, f"{site} installs the in-repo package: {offending}"


# Every tests/test_*.py that imports the package. Expects each to carry the
# benchmark_report marker or sit in HARNESS_TESTS_USING_PACKAGE. A new
# converter test with neither would only ever run against the release.
def test_package_importers_are_marked():
    unmarked = []
    for path in sorted((PROJECT_ROOT / "tests").glob("test_*.py")):
        if path.name in HARNESS_TESTS_USING_PACKAGE:
            continue
        text = path.read_text()
        if PACKAGE_IMPORT.search(text) and "mark.benchmark_report" not in text:
            unmarked.append(path.name)
    assert not unmarked, (
        f"import the package without the benchmark_report marker: {unmarked}"
    )
