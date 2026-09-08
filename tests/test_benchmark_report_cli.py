"""Tests for the ``benchmark-report`` CLI, using guidellm as the input format.

guidellm writes one ``results.json`` holding every benchmark of a run (one per
profile stage), so it is the only harness whose CLI path has to choose between
converting a single stage (``-i``) and converting all of them. That choice is
what these tests pin:

  - ``-i <n>`` writes exactly one report, to the given filename verbatim.
  - No ``-i`` writes one report per benchmark, suffixed ``_0``, ``_1``, ...

``-i 0`` is called out explicitly. It used to be swallowed by a truthiness
check and silently fall through to all-benchmarks mode, so asking for the
first stage gave you every stage instead -- no error, just N files where one
was requested.

The CLI is exercised through the same entry point operators and
``llmdbenchmark/analysis/scripts/guidellm-analyze_results.sh`` use, rather than
the converter functions directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llmdbenchmark.analysis.benchmark_report.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "guidellm_report_v2.json"

# The fixture is a two-stage constant profile at 2 and 4 req/s. Rate is what
# distinguishes one stage's report from the other's, so it is how these tests
# tell which benchmark actually got converted.
STAGE_RATES = [2.0, 4.0]


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    """Invoke the CLI as if from the command line."""
    monkeypatch.setattr("sys.argv", ["benchmark-report", *argv])
    main()


def _rate_of(report_file: Path) -> float:
    with open(report_file) as handle:
        report = yaml.safe_load(handle)
    return report["scenario"]["load"]["standardized"]["rate_qps"]


@pytest.mark.parametrize("index", [0, 1])
def test_index_converts_only_that_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index: int
) -> None:
    """``-i 0`` must behave like any other index: one report, that stage."""
    output = tmp_path / "br.yaml"

    _run(
        monkeypatch,
        str(FIXTURE),
        str(output),
        "-b",
        "0.2",
        "-w",
        "guidellm",
        "-i",
        str(index),
    )

    # Written to the requested name, with no "_<n>" suffix appended.
    assert [path.name for path in tmp_path.iterdir()] == ["br.yaml"]
    assert _rate_of(output) == STAGE_RATES[index]


def test_no_index_converts_every_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``-i``, every stage is converted to its own suffixed file."""
    _run(
        monkeypatch,
        str(FIXTURE),
        str(tmp_path / "br.yaml"),
        "-b",
        "0.2",
        "-w",
        "guidellm",
    )

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "br_0.yaml",
        "br_1.yaml",
    ]
    assert _rate_of(tmp_path / "br_0.yaml") == STAGE_RATES[0]
    assert _rate_of(tmp_path / "br_1.yaml") == STAGE_RATES[1]


def test_index_0_is_not_all_benchmarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the two modes must not produce the same thing for index 0.

    Stated as its own test because the bug was silent -- ``-i 0`` produced
    valid output, just not the output asked for.
    """
    selected, every = tmp_path / "one", tmp_path / "all"
    selected.mkdir()
    every.mkdir()

    _run(
        monkeypatch,
        str(FIXTURE),
        str(selected / "br.yaml"),
        "-b",
        "0.2",
        "-w",
        "guidellm",
        "-i",
        "0",
    )
    _run(
        monkeypatch, str(FIXTURE), str(every / "br.yaml"), "-b", "0.2", "-w", "guidellm"
    )

    assert len(list(selected.iterdir())) == 1
    assert len(list(every.iterdir())) == 2


def test_index_0_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """With no output file, ``-i 0`` prints one report -- and notably not the
    "# Benchmark 1 of N" banner that all-benchmarks mode emits."""
    _run(monkeypatch, str(FIXTURE), "-b", "0.2", "-w", "guidellm", "-i", "0")

    out = capsys.readouterr().out
    assert "# Benchmark" not in out
    assert (
        yaml.safe_load(out)["scenario"]["load"]["standardized"]["rate_qps"]
        == (STAGE_RATES[0])
    )


def test_index_0_refuses_to_clobber_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing-output guard has to cover the selected-index path too."""
    output = tmp_path / "br.yaml"
    output.write_text("do not overwrite me\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        _run(
            monkeypatch,
            str(FIXTURE),
            str(output),
            "-b",
            "0.2",
            "-w",
            "guidellm",
            "-i",
            "0",
        )

    assert exit_info.value.code == 1
    assert output.read_text(encoding="utf-8") == "do not overwrite me\n"

    # ...and --force still gets through it.
    _run(
        monkeypatch,
        str(FIXTURE),
        str(output),
        "-b",
        "0.2",
        "-w",
        "guidellm",
        "-i",
        "0",
        "--force",
    )
    assert _rate_of(output) == STAGE_RATES[0]
