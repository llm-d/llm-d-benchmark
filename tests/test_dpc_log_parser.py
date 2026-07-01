"""Tests for DPC log parser that extracts per-request timing from klog output."""

import pytest

from workload.harnesses.dpc_log_parser import (
    parse_dpc_log,
    parse_dpc_log_file,
)


# Representative klog lines from a DPC running with centralized "HTTP call done" line at V(5)
# The DPC now emits a single klog line per HTTP call with purpose="<token>" field.
SAMPLE_LOG = """\
I0603 15:29:00.100000  1 inference-server.go:145] "Reconciling server" serverUID="abc-123" requesterName="fma-req-1-1717424983-abcde"
I0603 15:29:01.200000  1 inference-server.go:2074] "HTTP call done" purpose="wake" method="POST" url="http://10.0.0.1:8005/wake_up" requesterName="fma-req-1-1717424983-abcde" httpCallStartTime="2026-06-03T15:29:01.150000000Z" latencySecs="0.51" statusCode="200"
I0603 15:29:03.500000  1 inference-server.go:2074] "HTTP call done" purpose="relay_ready" method="POST" url="http://10.0.0.2:8888/v1/become-ready" requesterName="fma-req-1-1717424983-abcde" httpCallStartTime="2026-06-03T15:29:03.480000000Z" latencySecs="0.02" statusCode="200"
I0603 15:30:10.000000  1 inference-server.go:2074] "HTTP call done" purpose="create_instance" method="POST" url="http://10.0.0.3:8888/v1/models" requesterName="fma-req-2-1717424983-fghij" httpCallStartTime="2026-06-03T15:30:09.900000000Z" latencySecs="0.10" statusCode="200"
I0603 15:30:50.000000  1 inference-server.go:2074] "HTTP call done" purpose="relay_ready" method="POST" url="http://10.0.0.3:8888/v1/become-ready" requesterName="fma-req-2-1717424983-fghij" httpCallStartTime="2026-06-03T15:30:49.950000000Z" latencySecs="0.05" statusCode="200"
I0603 15:31:00.000000  1 inference-server.go:667] "Created launcher-based server-providing pod" name="launcher-xq8lg" gpus="GPU-abc" requesterName="fma-req-3-1717424983-klmno" k8sCallStartTime="2026-06-03T15:30:59.800000000Z"
I0603 15:32:05.000000  1 inference-server.go:2074] "HTTP call done" purpose="relay_ready" method="POST" url="http://10.0.0.4:8888/v1/become-ready" requesterName="fma-req-3-1717424983-klmno" httpCallStartTime="2026-06-03T15:32:04.900000000Z" latencySecs="0.10" statusCode="200"
"""


class TestParseDpcLog:
    """Test parse_dpc_log extracts timing records per requester."""

    def test_hot_start_timing(self):
        records = parse_dpc_log(SAMPLE_LOG.splitlines())
        rec = records["fma-req-1-1717424983-abcde"]
        assert rec.relay_readiness_time is not None
        assert rec.wake_start_time is not None
        assert rec.instance_create_start_time is None
        assert rec.launcher_create_start_time is None
        t_hot = rec.t_hot()
        assert t_hot is not None
        assert 2.3 < t_hot < 2.4  # 15:29:03.48 - 15:29:01.15 = 2.33s

    def test_warm_start_timing(self):
        records = parse_dpc_log(SAMPLE_LOG.splitlines())
        rec = records["fma-req-2-1717424983-fghij"]
        assert rec.instance_create_start_time is not None
        assert rec.relay_readiness_time is not None
        assert rec.wake_start_time is None
        t_warm = rec.t_instance_create()
        assert t_warm is not None
        assert 40.0 < t_warm < 40.1  # 15:30:49.95 - 15:30:09.90 = 40.05s

    def test_cold_launcher_timing(self):
        records = parse_dpc_log(SAMPLE_LOG.splitlines())
        rec = records["fma-req-3-1717424983-klmno"]
        assert rec.launcher_create_start_time is not None
        assert rec.relay_readiness_time is not None
        t_cold = rec.t_cold_launcher()
        assert t_cold is not None
        assert 65.0 < t_cold < 65.2  # 15:32:04.90 - 15:30:59.80 = 65.1s

    def test_unknown_requester_not_present(self):
        records = parse_dpc_log(SAMPLE_LOG.splitlines())
        assert "nonexistent-pod" not in records

    def test_empty_log(self):
        records = parse_dpc_log([])
        assert records == {}

    def test_log_with_no_timing_fields(self):
        records = parse_dpc_log(
            ['I0603 15:29:00.100000  1 foo.go:1] "Something unrelated"']
        )
        assert records == {}


class TestParseDpcLogFile:
    """Test file-based entry point."""

    def test_reads_log_file_and_parses(self, tmp_path):
        log_file = tmp_path / "dpctlr-pod--manager.log"
        log_file.write_text(SAMPLE_LOG)
        records = parse_dpc_log_file(str(tmp_path))
        assert "fma-req-1-1717424983-abcde" in records
        assert records["fma-req-1-1717424983-abcde"].t_hot() is not None

    def test_missing_directory_returns_empty(self, tmp_path):
        records = parse_dpc_log_file(str(tmp_path / "nonexistent"))
        assert records == {}

    def test_no_matching_files_returns_empty(self, tmp_path):
        (tmp_path / "unrelated.log").write_text("nothing relevant here")
        records = parse_dpc_log_file(str(tmp_path))
        assert records == {}

    def test_indicator_message_past_256kb_still_parsed(self, tmp_path):
        """A DPC log whose first indicator message lands past 256KB must still parse.

        Regression for the original _is_dpc_log_file heuristic, which only sniffed
        the first 256KB. Real controller logs prepend large amounts of startup noise
        (flag dumps, leader election, reconcile churn) before the first
        relay/wake/create message, so the indicator can sit well past that boundary.
        """
        filler_line = (
            "I0603 15:00:00.000000  1 inference-server.go:145] "
            '"Reconciling server" serverUID="noise" requesterName="noise-pod"\n'
        )
        # Prepend well over 256KB of benign filler, then the real sample block.
        filler = filler_line * (300 * 1024 // len(filler_line) + 1)
        assert len(filler) > 256 * 1024
        log_file = tmp_path / "dpctlr-pod--manager.log"
        log_file.write_text(filler + SAMPLE_LOG)

        records = parse_dpc_log_file(str(tmp_path))
        assert "fma-req-1-1717424983-abcde" in records
        assert records["fma-req-1-1717424983-abcde"].t_hot() is not None

    def test_file_without_relay_but_with_wake_still_parsed(self, tmp_path):
        """DPC log where requester crashed before relay should still be found."""
        partial_log = (
            'I0603 15:29:01.000000  1 x.go:2074] "HTTP call done" purpose="wake" '
            'requesterName="req-crashed" httpCallStartTime="2026-06-03T15:29:00.900Z"\n'
        )
        (tmp_path / "dpctlr--manager.log").write_text(partial_log)
        records = parse_dpc_log_file(str(tmp_path))
        assert "req-crashed" in records
        assert records["req-crashed"].wake_start_time is not None
        assert records["req-crashed"].t_hot() is None  # no relay = no duration


class TestEdgeCases:
    """Edge cases for DPC log parsing robustness."""

    def test_multiple_relay_readiness_uses_last(self):
        """If DPC retries readiness relay, use the last successful one."""
        lines = [
            'I0603 15:29:01.000000  1 x.go:2074] "HTTP call done" purpose="wake" '
            'requesterName="req-retry" httpCallStartTime="2026-06-03T15:29:00.900Z"',
            'I0603 15:29:03.000000  1 x.go:2074] "HTTP call done" purpose="relay_ready" '
            'requesterName="req-retry" '
            'httpCallStartTime="2026-06-03T15:29:02.500Z"',
            'I0603 15:29:05.000000  1 x.go:2074] "HTTP call done" purpose="relay_ready" '
            'requesterName="req-retry" '
            'httpCallStartTime="2026-06-03T15:29:04.800Z"',
        ]
        records = parse_dpc_log(lines)
        rec = records["req-retry"]
        t_hot = rec.t_hot()
        assert t_hot is not None
        assert 3.8 < t_hot < 4.0  # 04.8 - 00.9 = 3.9s

    def test_unready_relay_ignored(self):
        """purpose='relay_unready' lines should not set relay_readiness_time."""
        lines = [
            'I0603 15:29:01.000000  1 x.go:2074] "HTTP call done" purpose="wake" '
            'requesterName="req-unready" httpCallStartTime="2026-06-03T15:29:00.900Z"',
            'I0603 15:29:03.000000  1 x.go:2074] "HTTP call done" purpose="relay_unready" '
            'requesterName="req-unready" '
            'httpCallStartTime="2026-06-03T15:29:02.500Z"',
        ]
        records = parse_dpc_log(lines)
        rec = records["req-unready"]
        assert rec.relay_readiness_time is None
        assert rec.t_hot() is None

    def test_malformed_timestamp_skipped(self):
        """Malformed httpCallStartTime should not crash, just skip."""
        lines = [
            'I0603 15:29:01.000000  1 x.go:2074] "HTTP call done" purpose="wake" '
            'requesterName="req-bad" httpCallStartTime="not-a-timestamp"',
            'I0603 15:29:03.000000  1 x.go:2074] "HTTP call done" purpose="relay_ready" '
            'requesterName="req-bad" '
            'httpCallStartTime="2026-06-03T15:29:02.500Z"',
        ]
        records = parse_dpc_log(lines)
        rec = records["req-bad"]
        assert rec.wake_start_time is None
        assert rec.relay_readiness_time is not None

    def test_multiple_requesters_independent(self):
        """Different requesters get independent records."""
        lines = [
            'I0603 15:29:01.000000  1 x.go:2074] "HTTP call done" purpose="wake" '
            'requesterName="req-a" httpCallStartTime="2026-06-03T15:29:00.000Z"',
            'I0603 15:30:01.000000  1 x.go:2074] "HTTP call done" purpose="create_instance" '
            'requesterName="req-b" httpCallStartTime="2026-06-03T15:30:00.000Z"',
            'I0603 15:29:05.000000  1 x.go:2074] "HTTP call done" purpose="relay_ready" '
            'requesterName="req-a" '
            'httpCallStartTime="2026-06-03T15:29:04.000Z"',
            'I0603 15:31:05.000000  1 x.go:2074] "HTTP call done" purpose="relay_ready" '
            'requesterName="req-b" '
            'httpCallStartTime="2026-06-03T15:31:04.000Z"',
        ]
        records = parse_dpc_log(lines)
        assert records["req-a"].t_hot() == pytest.approx(4.0, abs=0.01)
        assert records["req-b"].t_instance_create() == pytest.approx(64.0, abs=0.01)
        assert records["req-a"].t_instance_create() is None
        assert records["req-b"].t_hot() is None
