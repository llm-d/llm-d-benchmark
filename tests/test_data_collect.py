"""Tests for --data-collect: mode resolution, refused combinations, PVC reader."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from _toolchain import requires_compress_tools

from llmdbenchmark.cli import (
    _resolve_data_collect,
    _validate_data_collect,
    _warn_deprecated_analyze,
)
from llmdbenchmark.interface import run as run_interface
from llmdbenchmark.utilities.archive import (
    RemoteReader,
    RemoteReadError,
    read_member,
    read_member_remote,
    read_members,
    read_members_remote,
    remote_compress_script,
)


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def log_info(self, *_, **__):
        pass

    def log_warning(self, msg, **_):
        self.warnings.append(msg)

    def log_error(self, *_, **__):
        pass


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    run_interface.add_subcommands(subparsers, parents=[])
    return parser.parse_args(argv)


def _resolve(argv: list[str]) -> tuple[str, _Logger]:
    logger = _Logger()
    return _resolve_data_collect(_parse(argv), logger), logger


def test_deprecated_flag_maps_to_fast_and_warns() -> None:
    mode, logger = _resolve(["run", "--fast-collect"])
    assert mode == "fast"
    assert any("deprecated" in w for w in logger.warnings)


def test_explicit_mode_wins_over_deprecated_flag_and_env(monkeypatch) -> None:
    monkeypatch.setenv("LLMDBENCH_DATA_COLLECT", "results")
    mode, logger = _resolve(["run", "--fast-collect", "--data-collect", "skip"])
    assert mode == "skip"
    assert logger.warnings == []


def test_resolving_twice_warns_once() -> None:
    """The experiment loop's shape: one Namespace, one _do_run per treatment."""
    args = _parse(["run", "--fast-collect"])
    logger = _Logger()
    _resolve_data_collect(args, logger)
    _resolve_data_collect(args, logger)
    assert len(logger.warnings) == 1


@pytest.mark.parametrize("extra", [["--no-pvc"], ["-z"]])
def test_skip_refuses_what_it_cannot_honour(extra) -> None:
    args = _parse(["run", "--data-collect", "skip", *extra])
    logger = _Logger()
    _resolve_data_collect(args, logger)
    with pytest.raises(SystemExit):
        _validate_data_collect(args, logger)


def test_analyze_is_ignored_with_a_warning(monkeypatch) -> None:
    monkeypatch.delenv("LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY", raising=False)
    args = _parse(["run", "--data-collect", "skip", "--analyze"])
    logger = _Logger()
    _resolve_data_collect(args, logger)
    _validate_data_collect(args, logger)
    _warn_deprecated_analyze(args, logger)
    assert any("--analyze" in w for w in logger.warnings)

    quiet = _Logger()
    _warn_deprecated_analyze(_parse(["run"]), quiet)
    assert quiet.warnings == []


def test_analyze_env_var_is_ignored_with_a_warning(monkeypatch) -> None:
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY", "1")
    logger = _Logger()
    _warn_deprecated_analyze(_parse(["run"]), logger)
    assert len(logger.warnings) == 1


def test_analyze_is_hidden_from_help() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    run_interface.add_subcommands(subparsers, parents=[])
    run_parser = subparsers.choices["run"]
    assert "--analyze" not in run_parser.format_help()


def test_skip_with_cloud_output_warns_but_proceeds() -> None:
    """LLMDBENCH_OUTPUT makes this reachable without the user typing -r."""
    args = _parse(["run", "--data-collect", "skip", "-r", "gs://bucket/x"])
    logger = _Logger()
    _resolve_data_collect(args, logger)
    _validate_data_collect(args, logger)
    assert any("gs://bucket/x" in w for w in logger.warnings)


# ---------------------------------------------------------------------------
# The PVC reader
# ---------------------------------------------------------------------------


class _ExecResult:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class _FakeCmd:
    """Answers exec probes out of a local dir standing in for the pod."""

    _kube_bin = "kubectl"

    def __init__(self, test_exit=None):
        self.test_exit = test_exit

    def _kubeconfig_args(self):
        return []

    def kube_exec(self, _pod, *argv, **_kwargs):
        if argv[0] == "sh":
            if self.test_exit is not None:
                return _ExecResult(exit_code=self.test_exit, stderr="tunnel died")
            path = re.search(r"test -f (\S+)", argv[2]).group(1).strip("'")
            present = Path(path).is_file()
            return _ExecResult(
                stdout="__LLMDBENCH_PRESENT__" if present else "__LLMDBENCH_ABSENT__"
            )
        if argv[0] == "find":
            found = sorted(str(p) for p in Path(argv[1]).rglob("*") if p.is_file())
            return _ExecResult(stdout="\n".join(found))
        raise AssertionError(f"unexpected exec: {argv}")


def _reader(**kwargs) -> RemoteReader:
    reader = RemoteReader(_FakeCmd(**kwargs), "data-pod", "bench")
    reader._source = lambda path: subprocess.Popen(
        ["zstd", "-dc", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    reader.cat = lambda path: Path(path).read_bytes()
    return reader


class _SentinelCmd(_FakeCmd):
    def __init__(self, stdout="", exit_code=0, stderr=""):
        super().__init__()
        self._r = _ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    def kube_exec(self, *_a, **_k):
        return self._r


def _context(**kwargs):
    from llmdbenchmark.executor.context import ExecutionContext

    return ExecutionContext(
        plan_dir=Path("/tmp"), workspace=Path("/tmp"), logger=_Logger(), **kwargs
    )


def _result_set(root: Path) -> Path:
    d = root / "exp_1"
    (d / "analysis").mkdir(parents=True)
    (d / "summary_lifecycle_metrics.json").write_text(
        json.dumps({"failures": {"count": 0}}), encoding="utf-8"
    )
    (d / "analysis" / "stage_1_session_lifecycle_metrics.json").write_text(
        json.dumps({"stage_metadata": {"status": "COMPLETED"}}), encoding="utf-8"
    )
    return d


def _compress(directory: Path) -> None:
    result = subprocess.run(
        ["bash", "-c", remote_compress_script(str(directory), level=1)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_absent_member_reads_as_none(tmp_path) -> None:
    d = _result_set(tmp_path)
    assert read_member_remote(_reader(), str(d), "nope.json") is None


def test_transport_failure_raises_instead_of_reading_as_absent(tmp_path) -> None:
    d = _result_set(tmp_path)
    with pytest.raises(RemoteReadError):
        read_member_remote(
            _reader(test_exit=255), str(d), "summary_lifecycle_metrics.json"
        )


@pytest.mark.parametrize(
    "compressed",
    [False, pytest.param(True, marks=requires_compress_tools)],
)
def test_remote_reads_match_local_ones(tmp_path, compressed) -> None:
    """Same bytes and same glob set, whichever transport and whether archived."""
    d = _result_set(tmp_path)
    name = "summary_lifecycle_metrics.json"
    pattern = "analysis/stage_*_session_lifecycle_metrics.json"
    payload, matches = read_member(d, name), read_members(d, pattern)
    assert payload and set(matches) == {
        "analysis/stage_1_session_lifecycle_metrics.json"
    }

    if compressed:
        _compress(d)
        assert (d / "workspace.tar.zst").is_file()

    assert read_member_remote(_reader(), str(d), name) == payload
    assert read_members_remote(_reader(), str(d), pattern) == matches


def _capture_argv(monkeypatch, src, local, dir_compressed, members) -> list[str]:
    """The kube argv _copy_dir_from_pod would stream from, without running it."""
    from llmdbenchmark.run.steps import step_07_deploy_harness as dh

    seen: dict = {}

    def _capture(argv, _local_path):
        seen["argv"] = argv
        return type("R", (), {"success": True, "exit_code": 0, "stderr": ""})()

    with monkeypatch.context() as patch:
        patch.setattr(dh.DeployHarnessStep, "_fast_collect_stream", _capture)
        dh.DeployHarnessStep._copy_dir_from_pod(
            _FakeCmd(),
            "pod",
            "ns",
            str(src),
            local,
            _context(harness_data_collect="results" if members else "fast"),
            fast_collect=True,
            dir_compressed=dir_compressed,
            members=members,
        )
    return seen["argv"]


@requires_compress_tools
@pytest.mark.parametrize("pod_zstd", [True, False])
@pytest.mark.parametrize(
    "dir_compressed,members",
    [
        (False, None),
        (True, None),
        (False, ("benchmark_report*.yaml", "run_metadata.yaml")),
        (True, ("benchmark_report*.yaml", "run_metadata.yaml")),
    ],
)
def test_fast_collect_stream_round_trips_a_real_pod_pipeline(
    tmp_path, monkeypatch, dir_compressed, members, pod_zstd
):
    """Every remote command _copy_dir_from_pod builds, run by a real shell,
    extracts on the driver -- zstd or gzip, plain tree or compressed set.

    A real tar catches `tar cf -C dir .`: it writes a file named `-C` and
    streams nothing.
    """
    from llmdbenchmark.run.steps import step_07_deploy_harness as dh

    src = _result_set(tmp_path)
    (src / "benchmark_report_v0.2.yaml").write_text("report: yes", encoding="utf-8")
    (src / "run_metadata.yaml").write_text("meta: yes", encoding="utf-8")
    if dir_compressed:
        _compress(src)

    argv = _capture_argv(monkeypatch, src, tmp_path / "unused", dir_compressed, members)
    remote = argv[argv.index("--") + 1 :]
    remote[0] = shutil.which(remote[0])
    if not pod_zstd:
        # A pod without zstd: everything on PATH but zstd.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for tool in ("tar", "gzip", "find", "cat"):
            (bin_dir / tool).symlink_to(shutil.which(tool))
        monkeypatch.setenv("PATH", str(bin_dir))

    out = tmp_path / "collected"
    out.mkdir()
    result = dh.DeployHarnessStep._fast_collect_stream(remote, out)
    assert result.success, result.stderr

    assert (out / "benchmark_report_v0.2.yaml").read_text(
        encoding="utf-8"
    ) == "report: yes"
    if members:
        assert sorted(p.name for p in out.iterdir()) == [
            "benchmark_report_v0.2.yaml",
            "run_metadata.yaml",
        ]
    elif dir_compressed:
        assert (out / "workspace.tar.zst").is_file()
        assert not (out / "analysis").exists()
    else:
        assert (out / "analysis" / "stage_1_session_lifecycle_metrics.json").is_file()


def test_fast_collect_stream_survives_a_noisy_stderr(tmp_path):
    """Far more stderr than a pipe buffer holds, written before any stdout."""
    from llmdbenchmark.run.steps import step_07_deploy_harness as dh

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")
    script = (
        "head -c 1000000 /dev/zero | tr '\\0' x >&2; "
        f"tar cf - -C {shlex.quote(str(src))} ."
    )
    out = tmp_path / "out"
    out.mkdir()

    def _run():
        return dh.DeployHarnessStep._fast_collect_stream(["bash", "-c", script], out)

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(_run).result(timeout=60)

    assert result.success, result.stderr[:200]
    assert len(result.stderr) == 1_000_000
    assert (out / "a.txt").read_text(encoding="utf-8") == "a"


def test_fast_collect_stream_reports_a_failed_remote(tmp_path):
    from llmdbenchmark.run.steps import step_07_deploy_harness as dh

    result = dh.DeployHarnessStep._fast_collect_stream(
        ["bash", "-c", "echo 'tar: boom' >&2; exit 2"], tmp_path
    )

    assert not result.success
    assert "tar: boom" in result.stderr


def test_remote_reader_builds_a_valid_exec_argv() -> None:
    """The argv is otherwise never asserted: a wrong one 404s in the pod only."""
    reader = RemoteReader(_FakeCmd(), "data-pod", "bench")
    argv = reader._exec_argv("cat", "/requests/x.json")
    assert argv[:1] == ["kubectl"]
    assert (
        argv[-4:] == ["exec", "data-pod", "--", "cat"] or argv[-1] == "/requests/x.json"
    )
    assert "--namespace" in argv and "bench" in argv
    assert argv.index("--") < argv.index("cat")


def test_exists_reads_the_sentinel_not_the_exit_code() -> None:
    """kubectl returns 1 for a missing pod too, so exit 1 cannot mean "absent"."""
    reader = RemoteReader(
        _SentinelCmd(stdout="__LLMDBENCH_ABSENT__", exit_code=0), "p", "ns"
    )
    assert reader.exists("/requests/x") is False
    reader = RemoteReader(
        _SentinelCmd(stdout="__LLMDBENCH_PRESENT__", exit_code=0), "p", "ns"
    )
    assert reader.exists("/requests/x") is True
    # No sentinel: kubectl itself failed, whatever the code.
    reader = RemoteReader(
        _SentinelCmd(stdout="", exit_code=1, stderr="pod not found"), "p", "ns"
    )
    with pytest.raises(RemoteReadError):
        reader.exists("/requests/x")


@requires_compress_tools
def test_dropped_exec_stream_raises_instead_of_reading_as_absent(tmp_path) -> None:
    """The upstream cat, not zstd, holds the cause of a truncated archive."""
    d = _result_set(tmp_path)
    _compress(d)
    reader = RemoteReader(_FakeCmd(), "p", "ns")
    reader.cat = lambda path: Path(path).read_bytes()

    def _broken(_path):
        proc = subprocess.Popen(
            ["sh", "-c", "echo -n garbage; exit 1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.upstream = subprocess.Popen(
            ["sh", "-c", "echo 'error: unable to upgrade connection' >&2; exit 1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return proc

    reader._source = _broken
    with pytest.raises(RemoteReadError):
        read_member_remote(reader, str(d), "summary_lifecycle_metrics.json")
