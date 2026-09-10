"""Tests for --data-collect: mode resolution, refused combinations, PVC reader."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import pytest

from llmdbenchmark.cli import _resolve_data_collect, _validate_data_collect
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


@pytest.mark.parametrize("extra", [["--analyze"], ["--no-pvc"], ["-z"]])
def test_skip_refuses_what_it_cannot_honour(extra) -> None:
    args = _parse(["run", "--data-collect", "skip", *extra])
    logger = _Logger()
    _resolve_data_collect(args, logger)
    with pytest.raises(SystemExit):
        _validate_data_collect(args, logger)


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


@pytest.mark.parametrize("compressed", [False, True])
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


@pytest.mark.parametrize(
    "dir_compressed,members,expect_flags,expect_mode",
    [
        (False, None, "cz", "r|gz"),
        (True, None, "cf", "r|"),
        (False, ("*.png",), "cz", "r|gz"),
        (True, ("*.png",), "cz", "r|gz"),
    ],
)
def test_remote_tar_flags_and_extraction_mode_agree(
    monkeypatch, tmp_path, dir_compressed, members, expect_flags, expect_mode
) -> None:
    """A gzip'd stream read as plain (or the reverse) fails extraction outright."""
    from llmdbenchmark.run.steps import step_07_deploy_harness as dh

    seen: dict = {}

    def _stream(argv, local_path, mode="r|gz"):
        seen["argv"], seen["mode"] = argv, mode
        return type("R", (), {"success": True, "exit_code": 0, "stderr": ""})()

    monkeypatch.setattr(dh.DeployHarnessStep, "_fast_collect_stream", _stream)
    dh.DeployHarnessStep._copy_dir_from_pod(
        _FakeCmd(),
        "pod",
        "ns",
        "/requests/exp_1",
        tmp_path,
        _context(harness_data_collect="results" if members else "fast"),
        fast_collect=True,
        dir_compressed=dir_compressed,
        members=members,
    )
    assert seen["mode"] == expect_mode
    assert expect_flags in " ".join(seen["argv"])


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
