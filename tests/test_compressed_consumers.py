"""Every post-collection consumer has to work on a compressed result set.

Compression deletes the originals, so a consumer that only reads plain paths does
not raise -- it silently reports nothing, which reads as "the benchmark found no
data" rather than "the reader is broken". These pin the readers that were converted
for that reason; each one failed before conversion.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from llmdbenchmark.utilities.archive import remote_compress_script

# Skipping locally is fine; skipping in CI would mean this whole file -- every guard
# on the only copy of a result set -- passes vacuously, which is how it went unnoticed
# that CI had no zstd at all.
if shutil.which("zstd") is None and os.environ.get("CI"):
    raise RuntimeError("zstd missing in CI: these tests would skip silently")

pytestmark = pytest.mark.skipif(
    shutil.which("zstd") is None, reason="needs the zstd CLI"
)


def _compress(directory: Path, expect_ok: bool = True, env: dict | None = None):
    """Run the real PVC script locally. ``expect_ok=False`` for the guard tests,
    which assert it refuses rather than deletes."""
    result = subprocess.run(
        ["bash", "-c", remote_compress_script(str(directory), level=1)],
        capture_output=True,
        check=False,
        env=env,
    )
    if expect_ok:
        assert result.returncode == 0, result.stderr
    return result


def test_metrics_embedding_clips_each_stage_in_pod(tmp_path):
    """One scrape covers the whole run, so each stage report needs its own window.

    The in-pod analyzers used to inline a one-liner that passed no window, leaving
    every stage carrying the whole run -- clipping existed only on the driver, which
    no longer re-analyses a set the pod already handled.
    """
    import yaml

    from llmdbenchmark.analysis.metrics_embed import embed_metrics, stage_windows

    results = tmp_path / "results"
    (results / "metrics" / "processed").mkdir(parents=True)
    (results / "stdout.log").write_text(
        "2026-08-18 10:00:00,1 INFO Stage 0 - run started\n"
        "2026-08-18 10:01:00,1 INFO Stage 0 - run completed\n"
        "2026-08-18 10:02:00,1 INFO Stage 1 - run started\n"
        "2026-08-18 10:03:00,1 INFO Stage 1 - run completed\n",
        encoding="utf-8",
    )
    for stage in (0, 1):
        (results / f"benchmark_report_v0.2,_stage_{stage}.json.yaml").write_text(
            "run:\n  uid: x\nresults: {}\n", encoding="utf-8"
        )
    (results / "metrics" / "processed" / "metrics_summary.json").write_text(
        '{"_aggregated": {"metrics": {}}}', encoding="utf-8"
    )

    assert sorted(stage_windows(results)) == [0, 1]
    assert embed_metrics(results / "metrics", results, log=None) == 2

    windows = {}
    for stage in (0, 1):
        report = results / f"benchmark_report_v0.2,_stage_{stage}.json.yaml"
        obs = yaml.safe_load(report.read_text())["results"]["observability"]
        interval = obs["time_series_interval"]
        windows[stage] = (interval["start"], interval["end"])

    # Distinct windows: identical ones mean the whole run was embedded in both.
    assert windows[0] != windows[1]
    assert windows[0][0].startswith("2026-08-18T10:00:00")
    assert windows[1][0].startswith("2026-08-18T10:02:00")


def test_every_harness_builds_its_report_in_the_pod():
    """The report is the pod's output, so collection is a pure transfer.

    eval-containers was the last harness without an analyzer, which made its report
    the one the driver had to build -- from a result set that by then is compressed.
    """
    from llmdbenchmark.analysis import _IN_POD_ANALYZERS, _WRITER_NAMES

    scripts = (
        Path(__file__).resolve().parents[1] / "llmdbenchmark" / "analysis" / "scripts"
    )
    for harness in sorted(_IN_POD_ANALYZERS):
        shell = scripts / f"{harness}-analyze_results.sh"
        python = scripts / f"{harness}-analyze_results.py"
        assert shell.is_file() or python.is_file(), f"{harness} ships no analyzer"

    # Anything the driver can write a report for must have an in-pod analyzer too,
    # or that report only ever exists after collection.
    assert set(_WRITER_NAMES) <= _IN_POD_ANALYZERS


def test_nop_gate_sees_in_pod_output_in_the_archive(tmp_path):
    """A plain check reads as "the pod did nothing" and hands the work to a driver
    path whose own input is archived too, failing the run."""
    from llmdbenchmark.analysis import pod_analysis_present, run_analysis

    results = tmp_path / "exp_1"
    (results / "analysis").mkdir(parents=True)
    (results / "benchmark_report").mkdir()
    (results / "analysis" / "result.txt").write_text("nop output\n", encoding="utf-8")
    (results / "benchmark_report" / "result.yaml").write_text(
        "x: 1\n", encoding="utf-8"
    )
    _compress(results)

    assert pod_analysis_present("nop", results)
    assert run_analysis("nop", results, None) is None


def test_eval_containers_report_is_gated_once_the_pod_built_it(tmp_path):
    """With the analyzer in place the driver must defer -- and must still fall back
    for a result set an older image left unanalysed."""
    from llmdbenchmark.analysis import pod_analysis_present, run_analysis

    built = tmp_path / "built"
    (built / "task").mkdir(parents=True)
    (built / "task" / "result.json").write_text(
        json.dumps({"benchmark": "polyglot", "reward": 1.0, "passed": True}),
        encoding="utf-8",
    )
    (built / "benchmark_report_v0.2,_result.json.yaml").write_text(
        "run: {}\n", encoding="utf-8"
    )
    assert pod_analysis_present("eval-containers", built)
    assert run_analysis("eval-containers", built, None) is None

    unanalysed = tmp_path / "unanalysed"
    (unanalysed / "task").mkdir(parents=True)
    (unanalysed / "task" / "result.json").write_text(
        json.dumps({"benchmark": "polyglot", "reward": 1.0, "passed": True}),
        encoding="utf-8",
    )
    assert not pod_analysis_present("eval-containers", unanalysed)
    assert run_analysis("eval-containers", unanalysed, None) is None
    assert list(unanalysed.glob("benchmark_report_v0.2,_*.yaml"))


def test_no_compress_leaves_the_same_artifacts_as_compress(tmp_path):
    """--no-compress must leave the same artifacts at the same paths: the only
    difference is whether the bulk sits in the archive or beside it."""
    from llmdbenchmark.utilities.archive import read_member

    def build(results: Path) -> None:
        (results / "analysis" / "distributions").mkdir(parents=True)
        (results / "metrics" / "graphs").mkdir(parents=True)
        (results / "benchmark_report_v0.2,_stage_0.json.yaml").write_text(
            "run: {}\n", encoding="utf-8"
        )
        (results / "run_metadata.yaml").write_text("model: m\n", encoding="utf-8")
        (results / "analysis" / "distributions" / "dist_ttft.png").write_bytes(b"PNG\n")
        (results / "metrics" / "graphs" / "kv.png").write_bytes(b"PNG\n")
        (results / "analysis" / "summary.txt").write_text("sum\n", encoding="utf-8")
        (results / "stdout.log").write_text("log\n", encoding="utf-8")
        (results / "per_request_lifecycle_metrics.json").write_text(
            '[{"id": 1}]\n' * 50, encoding="utf-8"
        )

    plain = tmp_path / "plain" / "exp_1"
    compressed = tmp_path / "compressed" / "exp_1"
    build(plain)
    build(compressed)
    _compress(compressed)

    # Hardcoded, not derived from KEEP_PLAIN: deriving it would make the test agree
    # with whatever that constant says, including a change that moves a plot or a
    # report into the archive. Globbed with '*' rather than the patterns, so the
    # shell's idea of what stays plain is checked against nothing but this literal.
    expected = {
        "benchmark_report_v0.2,_stage_0.json.yaml",
        "run_metadata.yaml",
        "analysis/distributions/dist_ttft.png",
        "metrics/graphs/kv.png",
    }
    assert expected == {
        str(f.relative_to(compressed))
        for f in compressed.rglob("*")
        if f.is_file() and ".tar." not in f.name
    }

    # Nothing lost: every archived file is still readable, without expanding.
    for archived in (
        "stdout.log",
        "analysis/summary.txt",
        "per_request_lifecycle_metrics.json",
    ):
        assert read_member(compressed, archived) == (plain / archived).read_bytes()


def test_member_names_keep_a_leading_dot():
    """``lstrip('./')`` is a character set, so it eats the dot of a real name:
    ``./.exit-code`` becomes ``exit-code`` and the eval-containers exit code reads
    as absent, which that roll-up cannot tell from a task that produced nothing."""
    from llmdbenchmark.utilities.archive import _strip_dot_slash

    assert _strip_dot_slash("./.exit-code") == ".exit-code"
    assert _strip_dot_slash("./agent/.exit-code") == "agent/.exit-code"


def test_plain_files_win_over_an_archive(tmp_path):
    """A partially-expanded tree must not be merged with its own archive, or a
    reader sees two generations of the same file."""
    from llmdbenchmark.utilities.archive import read_members

    results = tmp_path / "exp_1"
    (results / "metrics" / "raw").mkdir(parents=True)
    (results / "metrics" / "raw" / "a_metrics.log").write_text("OLD", encoding="utf-8")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")
    _compress(results)

    (results / "metrics" / "raw").mkdir(parents=True, exist_ok=True)
    (results / "metrics" / "raw" / "a_metrics.log").write_text("NEW", encoding="utf-8")

    got = read_members(results, "metrics/raw/*_metrics.log")
    assert got == {"metrics/raw/a_metrics.log": b"NEW"}

    # And the archive branch, which is the only reason this function exists: with no
    # plain copy it must still find the snapshot, not silently return nothing.
    (results / "metrics" / "raw" / "a_metrics.log").unlink()
    assert read_members(results, "metrics/raw/*_metrics.log") == {
        "metrics/raw/a_metrics.log": b"OLD"
    }


def test_a_corrupt_leftover_archive_is_rebuilt(tmp_path):
    """A crashed attempt leaves a truncated archive. Trusting it and then deleting
    against its member list is how the only copy of a result set disappears."""
    results = tmp_path / "exp_1"
    (results / "logs").mkdir(parents=True)
    (results / "logs" / "stdout.log").write_text("BULK", encoding="utf-8")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")
    (results / "workspace.tar.zst").write_bytes(b"\x00truncated garbage")

    _compress(results)

    # The archive has to be the rebuilt one, not the garbage: reading the log would
    # pass either way, because the early-exit path leaves the plain file in place.
    assert (
        subprocess.run(
            ["zstd", "-t", str(results / "workspace.tar.zst")], check=False
        ).returncode
        == 0
    )
    assert not (results / "logs" / "stdout.log").exists()

    from llmdbenchmark.utilities.archive import read_member

    assert read_member(results, "logs/stdout.log") == b"BULK"
    assert (results / "run_metadata.yaml").is_file()

    # None, not b"": pod_analysis_present treats "not None" as "the pod analysed
    # this", so an empty-bytes answer on a corrupt archive skips the driver fallback.
    archive = results / "workspace.tar.zst"
    archive.write_bytes(b"\x00not an archive")
    assert read_member(results, "logs/stdout.log") is None


def test_a_failing_keeper_probe_spares_the_directory(tmp_path):
    """The probe authorises the only ``rm -rf`` here. A find that fails prints
    nothing, and treating that as "no keepers" deletes files deliberately left out
    of the archive -- the one direction with no recovery."""
    results = tmp_path / "exp_1"
    (results / "plots").mkdir(parents=True)
    (results / "plots" / "latency.png").write_bytes(b"KEEPER")
    (results / "plots" / "bulk.log").write_text("BULK", encoding="utf-8")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")

    # Fail only the probe, which is the sole find called with a './<top>' argument.
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "find").write_text(
        "#!/bin/bash\n"
        "# Only the keeper probe passes a subdirectory as $1; every other call\n"
        '# passes ".", and failing those would abort before anything is packed.\n'
        'case "$1" in ./?*) exit 3 ;; esac\n'
        'exec /usr/bin/find "$@"\n',
        encoding="utf-8",
    )
    (shim / "find").chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")

    _compress(results, env=env)

    # The archive must exist, or the keeper survived because the script aborted
    # before packing rather than because the probe failed safe.
    assert (results / "workspace.tar.zst").is_file()
    assert (results / "plots" / "latency.png").read_bytes() == b"KEEPER"


def _listing_shims(tmp_path: Path, decompress_rc: int, listing: str) -> dict:
    """PATH shims that break the listing pipeline (``zstd -dc | tar tf -``) while
    leaving compression itself working."""
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    (shim / "zstd").write_text(
        f'#!/bin/bash\nif [ "$1" = "-dc" ]; then exit {decompress_rc}; fi\n'
        'exec /usr/bin/zstd "$@"\n',
        encoding="utf-8",
    )
    (shim / "tar").write_text(
        f'#!/bin/bash\nif [ "$1" = "tf" ]; then printf %s {listing!r}; exit 0; fi\n'
        'exec /usr/bin/tar "$@"\n',
        encoding="utf-8",
    )
    for name in ("zstd", "tar"):
        (shim / name).chmod(0o755)
    return dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")


def _packed(tmp_path: Path) -> Path:
    results = tmp_path / "exp_1"
    (results / "logs").mkdir(parents=True)
    (results / "logs" / "stdout.log").write_text("BULK", encoding="utf-8")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")
    return results


def test_a_failed_decompress_does_not_become_an_empty_delete_list(tmp_path):
    """Without pipefail a failing ``zstd -dc`` is invisible -- the pipeline's exit
    status is tar's -- so the loop deletes against a listing that was never read."""
    results = _packed(tmp_path)
    env = _listing_shims(tmp_path, decompress_rc=7, listing="./logs/stdout.log\n")

    done = _compress(results, expect_ok=False, env=env)

    assert done.returncode != 0
    assert (results / "logs" / "stdout.log").read_text() == "BULK"


def test_an_empty_listing_aborts_before_deleting(tmp_path):
    """An unreadable archive yields no members. Treating that as "nothing to delete"
    would pass silently over a tree whose originals are still the only copy."""
    results = _packed(tmp_path)
    env = _listing_shims(tmp_path, decompress_rc=0, listing="")

    done = _compress(results, expect_ok=False, env=env)

    assert done.returncode != 0
    assert (results / "logs" / "stdout.log").read_text() == "BULK"


@pytest.mark.parametrize(
    ("harness_settled", "wait_timeout", "expected"),
    [(True, 3600, True), (False, 3600, False), (True, 0, False)],
)
def test_only_a_settled_pvc_is_compressed(harness_settled, wait_timeout, expected):
    """Both halves gate the delete: a still-running harness writes files that land
    in no archive, and wait_timeout 0 means nothing ever saw it finish."""
    from llmdbenchmark.executor.context import ExecutionContext
    from llmdbenchmark.run.steps.step_07_deploy_harness import DeployHarnessStep

    context = ExecutionContext(plan_dir=Path("/x"), workspace=Path("/x"))
    context.harness_wait_timeout = wait_timeout

    assert DeployHarnessStep._pvc_settled(context, harness_settled) is expected


def test_awkwardly_named_keepers_are_not_deleted(tmp_path):
    """Two name-identity traps that each deleted a keeper: ``--exclude-from`` read
    ``plot[1].png`` as a glob so it did not match itself, and the delete loop's
    ``read`` stripped a trailing space onto the neighbouring keeper's name."""
    results = tmp_path / "exp_1"
    results.mkdir()
    (results / "plot[1].png").write_bytes(b"BRACKET")
    (results / "latency.png").write_bytes(b"KEEPER")
    (results / "latency.png ").write_text("BULK", encoding="utf-8")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")

    _compress(results)

    assert (results / "workspace.tar.zst").is_file()
    assert (results / "plot[1].png").read_bytes() == b"BRACKET"
    assert (results / "latency.png").read_bytes() == b"KEEPER"


def test_a_linked_member_reads_through_to_its_target(tmp_path):
    """tar stores a linked payload once and gives the other name a data-less entry,
    so reading that name has to follow the link or the file looks absent."""
    results = tmp_path / "exp_1"
    results.mkdir()
    (results / "a.json").write_text("SHARED", encoding="utf-8")
    (results / "b.json").hardlink_to(results / "a.json")
    (results / "c.json").symlink_to("a.json")
    # Nested, so a relative target has to be resolved against its own directory
    # rather than the result root.
    (results / "sub").mkdir()
    (results / "sub" / "d.json").write_text("NESTED", encoding="utf-8")
    (results / "sub" / "e.json").symlink_to("d.json")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")

    _compress(results)

    from llmdbenchmark.utilities.archive import read_member

    for name in ("a.json", "b.json", "c.json"):
        assert read_member(results, name) == b"SHARED", name
    for name in ("sub/d.json", "sub/e.json"):
        assert read_member(results, name) == b"NESTED", name


def test_a_parent_archive_is_not_read_as_this_result_sets(tmp_path):
    """The FMA arms sit side by side under results/, so searching outside the set
    would resolve one arm's file from a neighbour's archive and mislabel it."""
    from llmdbenchmark.utilities.archive import read_member

    results = tmp_path / "results"
    arm = results / "exp_b"
    arm.mkdir(parents=True)
    (arm / "hpa.txt").write_text("HPA-B\n", encoding="utf-8")
    (arm / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")
    _compress(arm)

    stray = results / "exp_a"
    stray.mkdir()
    (stray / "hpa.txt").write_text("HPA-A\n", encoding="utf-8")
    (stray / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")
    _compress(stray)
    (results / "workspace.tar.zst").write_bytes(
        (stray / "workspace.tar.zst").read_bytes()
    )

    assert read_member(arm, "hpa.txt") == b"HPA-B\n"
    assert read_member(results / "exp_c", "hpa.txt") is None


def test_a_newline_in_a_name_refuses_instead_of_deleting(tmp_path):
    """`find` writes a newline raw, splitting one skip-list entry into two bogus
    patterns, and `tar tf` escapes it -- so the delete loop can resolve one file's
    escaped rendering onto a different real path and remove bytes no archive holds.
    Refusing costs disk; compressing costs the only copy."""
    results = tmp_path / "exp_1"
    results.mkdir()
    (results / "x\\nb.png").write_bytes(b"KEEPER")  # literal backslash-n
    (results / "x\nb.png").write_bytes(b"other")  # real newline
    (results / "bulk.log").write_text("BULK", encoding="utf-8")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")

    assert _compress(results, expect_ok=False).returncode != 0

    assert not (results / "workspace.tar.zst").exists()
    assert (results / "x\\nb.png").read_bytes() == b"KEEPER"
    assert (results / "bulk.log").read_text() == "BULK"


def test_a_converter_exiting_does_not_kill_the_analysis_phase(tmp_path):
    """The converters share a CLI entry point whose input check calls sys.exit, and
    the driver fallback hands it a path that only exists inside the archive. A bare
    `except Exception` misses SystemExit, so it would take the run down."""
    results = tmp_path / "exp_1"
    results.mkdir()
    (results / "results.json").write_text(
        json.dumps({"benchmarks": []}), encoding="utf-8"
    )
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")
    _compress(results)

    from llmdbenchmark.analysis import run_analysis

    # A string is the "converted nothing" signal; SystemExit escaping is not.
    assert isinstance(run_analysis("guidellm", results, None), str)


def test_the_remote_dir_is_quoted_and_the_fallback_backend_reads():
    """Two holes with no other cover: the script interpolates a path straight into
    `cd`, and the pure-Python backend is never chosen while the zstd CLI is present."""
    from llmdbenchmark.utilities.archive import remote_compress_script

    script = remote_compress_script("/requests/exp 1; rm -rf /tmp/PWNED")
    assert "cd '/requests/exp 1; rm -rf /tmp/PWNED'" in script


def test_the_archive_never_becomes_a_member_of_itself(tmp_path):
    """The skip list is a snapshot, so an archive appearing after it was taken would
    be packed into itself -- and the delete loop, trusting the verified member list,
    would remove the one file holding everything."""
    results = tmp_path / "exp_1"
    (results / "logs").mkdir(parents=True)
    (results / "logs" / "stdout.log").write_text("BULK", encoding="utf-8")
    (results / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")

    # A shim standing in for a concurrent run: create the archive after the snapshot.
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "tar").write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = "cf" ]; then : > {results / "workspace.tar.zst"}; fi\n'
        'exec /usr/bin/tar "$@"\n',
        encoding="utf-8",
    )
    (shim / "tar").chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")

    _compress(results, env=env)

    from llmdbenchmark.utilities.archive import read_member

    assert read_member(results, "logs/stdout.log") == b"BULK"


def test_a_glob_matches_the_same_set_plain_or_archived(tmp_path):
    """fnmatch's `*` crosses `/` but Path.glob's does not, so the archive side would
    fold in a nested file the plain side excludes -- the same glob answering
    differently depending on whether the tree happens to be compressed."""
    from llmdbenchmark.utilities.archive import read_members

    def build(root: Path) -> None:
        (root / "metrics" / "raw" / "sub").mkdir(parents=True)
        (root / "metrics" / "raw" / "a_metrics.log").write_text("S1", encoding="utf-8")
        (root / "metrics" / "raw" / "sub" / "b_metrics.log").write_text(
            "S2", encoding="utf-8"
        )
        (root / "run_metadata.yaml").write_text("x: 1\n", encoding="utf-8")

    plain = tmp_path / "plain"
    archived = tmp_path / "archived"
    build(plain)
    build(archived)
    _compress(archived)

    pattern = "metrics/raw/*_metrics.log"
    assert read_members(archived, pattern) == read_members(plain, pattern)
    assert set(read_members(plain, pattern)) == {"metrics/raw/a_metrics.log"}


@pytest.mark.parametrize(
    ("settled", "driver_zstd", "pod_zstd", "expected", "probes"),
    [
        (True, True, True, True, 1),
        (False, True, True, False, 0),
        (True, False, True, False, 0),
        (True, True, False, False, 1),
    ],
)
def test_pvc_compression_needs_every_gate(
    monkeypatch, settled, driver_zstd, pod_zstd, expected, probes
):
    """Guards an irreversible delete, so all four gates must hold -- and the pod
    probe (a live `kubectl exec`) must not run once a cheaper gate has said no."""
    from llmdbenchmark.executor.context import ExecutionContext
    from llmdbenchmark.run.steps.step_07_deploy_harness import DeployHarnessStep

    calls = []
    monkeypatch.setattr(
        DeployHarnessStep,
        "_pvc_has_zstd",
        staticmethod(lambda *a: calls.append(a) or pod_zstd),
    )
    monkeypatch.setattr(
        "llmdbenchmark.run.steps.step_07_deploy_harness.shutil.which",
        lambda name: "/usr/bin/zstd" if driver_zstd else None,
    )

    warnings = []
    context = ExecutionContext(plan_dir=Path("/x"), workspace=Path("/x"))
    context.harness_wait_timeout = 3600
    context.logger = SimpleNamespace(log_warning=warnings.append)

    got = DeployHarnessStep._should_compress_on_pvc(None, context, "pod", "ns", settled)

    assert got is expected
    assert len(calls) == probes
    # Exactly one reason, so a declined compression is never silent or doubly
    # reported.
    assert len(warnings) == (0 if expected else 1)
