"""The shared stream layer: writer, sinks and reader."""

import datetime
import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from llmd_benchmark_report.artifacts import (
    LocalFileSink,
    Run,
    StreamWriter,
    read_stream,
)
from llmd_benchmark_report.artifacts.base import ArtifactHeader
from llmd_benchmark_report.artifacts.metric_time_series.v0_1.schema import (
    MetricTimeSeriesV01,
)
from llmd_benchmark_report.artifacts.request_log.v0_1.schema import (
    RequestLogV01,
    RequestRecord,
)

T0 = datetime.datetime(2026, 11, 17, 10, 0, tzinfo=datetime.UTC)


# Keeps each flushed segment as its own bytes, so tests can count flushes and
# cut the stream at a segment boundary.
class MemorySink:
    def __init__(self) -> None:
        self.segments: list[bytes] = []
        self.closed = False

    def write_segment(self, data: bytes) -> None:
        self.segments.append(data)

    def close(self) -> None:
        self.closed = True

    def data(self) -> bytes:
        return b"".join(self.segments)


# A clock the test moves by hand. Starts at 0.
class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# Request i was sent i seconds after T0.
def _request(i: int) -> RequestRecord:
    return RequestRecord(id=f"r{i}", sent=T0 + datetime.timedelta(seconds=i))


# A request-log writer on a fresh MemorySink with run uid "u1".
def _writer(**kwargs: Any) -> tuple[StreamWriter[Any], MemorySink]:
    sink = MemorySink()
    return RequestLogV01.open(sink, Run(uid="u1"), **kwargs), sink


# open() flushes the header at once: one segment, header kind request-log at
# 0.1 with run uid u1, no records, no end line.
def test_header_is_flushed_at_open() -> None:
    _, sink = _writer()
    assert len(sink.segments) == 1
    contents = read_stream(sink.data())
    assert contents.header == {
        "kind": "request-log",
        "version": "0.1",
        "run": {"uid": "u1"},
    }
    assert contents.lines == []
    assert contents.end is None


# 3 requests, then close(). The end line counts 3, the sink is closed, and
# read() gives back the 3 requests and complete=True.
def test_close_writes_the_end_line() -> None:
    writer, sink = _writer()
    for i in range(3):
        writer.write(_request(i))
    writer.close()
    assert sink.closed
    assert read_stream(sink.data()).end == {"records": 3}
    log, complete = RequestLogV01.read(sink.data())
    assert complete
    assert [r.id for r in log.requests] == ["r0", "r1", "r2"]


# A with block that raises after 2 requests flushes both and closes the sink,
# but writes no end line, so read() says complete=False.
def test_an_exception_leaves_the_stream_partial() -> None:
    sink = MemorySink()
    with pytest.raises(RuntimeError):
        with RequestLogV01.open(sink, Run(uid="u1")) as writer:
            writer.write(_request(0))
            writer.write(_request(1))
            raise RuntimeError("job died")
    assert sink.closed
    log, complete = RequestLogV01.read(sink.data())
    assert not complete
    assert len(log.requests) == 2


# flush_lines=1 puts each request in its own segment. Cutting the last 5 bytes
# off the 5th request's segment, inside its checksum, keeps the first 4 requests
# and reads as partial.
def test_a_cut_off_segment_keeps_every_segment_before_it() -> None:
    writer, sink = _writer(flush_lines=1)
    for i in range(5):
        writer.write(_request(i))
    writer.close()
    # segments: header, 5 requests, end line
    assert len(sink.segments) == 7
    cut = b"".join(sink.segments[:5]) + sink.segments[5][:-5]
    log, complete = RequestLogV01.read(cut)
    assert not complete
    assert [r.id for r in log.requests] == ["r0", "r1", "r2", "r3"]


# Segments joined end to end are one gzip file the stdlib reads whole, which is
# what joining uploaded segments in a bucket produces.
def test_joined_segments_are_one_gzip_file() -> None:
    writer, sink = _writer(flush_lines=1)
    writer.write(_request(0))
    writer.write(_request(1))
    writer.close()
    lines = gzip.decompress(sink.data()).decode().splitlines()
    assert [json.loads(line).get("id") for line in lines] == [None, "r0", "r1", None]


# flush_seconds=15 on a hand-moved clock: writes at 0s and 10s stay buffered, the
# write at 15s flushes. Only the header segment exists before that.
def test_flush_after_flush_seconds() -> None:
    clock = FakeClock()
    writer, sink = _writer(flush_lines=1000, flush_seconds=15, clock=clock)
    writer.write(_request(0))
    clock.now = 10
    writer.write(_request(1))
    assert len(sink.segments) == 1
    clock.now = 15
    writer.write(_request(2))
    assert len(sink.segments) == 2


# flush_lines=3 with a clock that never moves: the first 2 writes stay
# buffered, the 3rd flushes.
def test_flush_after_flush_lines() -> None:
    writer, sink = _writer(flush_lines=3, clock=FakeClock())
    writer.write(_request(0))
    writer.write(_request(1))
    assert len(sink.segments) == 1
    writer.write(_request(2))
    assert len(sink.segments) == 2


# An encoder line carrying the "$end" key is refused, so a record can never
# pass for the end line.
def test_records_cannot_use_the_end_key() -> None:
    class EndEncoder:
        def encode(self, record: int) -> list[dict[str, Any]]:
            return [{"$end": record}]

    header = ArtifactHeader(kind="request-log", version="0.1", run=Run(uid="u1"))
    writer = StreamWriter(MemorySink(), header, EndEncoder())
    with pytest.raises(ValueError, match="reserved"):
        writer.write(1)


# write() after close() raises.
def test_write_after_close_raises() -> None:
    writer, _ = _writer()
    writer.close()
    with pytest.raises(ValueError, match="after close"):
        writer.write(_request(0))


# An end line counting 5 records over a stream holding 1 is an error, not a
# partial read.
def test_end_line_must_match_the_records() -> None:
    lines = [
        {"kind": "request-log", "version": "0.1", "run": {"uid": "u1"}},
        _request(0).model_dump(mode="json", exclude_none=True),
        {"$end": {"records": 5}},
    ]
    data = gzip.compress("".join(json.dumps(x) + "\n" for x in lines).encode())
    with pytest.raises(ValueError, match="counts 5 records"):
        RequestLogV01.read(data)


# A request-log stream read as metric-time-series is refused and names both kinds.
def test_read_refuses_another_kind() -> None:
    _, sink = _writer()
    with pytest.raises(
        ValueError, match="metric-time-series 0.1, this stream is request-log 0.1"
    ):
        MetricTimeSeriesV01.read(sink.data())


# LocalFileSink appends every segment to one file that reads back like the
# segments joined, and refuses a path that already exists.
def test_local_file_sink(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl.gz"
    with RequestLogV01.open(
        LocalFileSink(path), Run(uid="u1"), flush_lines=1
    ) as writer:
        writer.write(_request(0))
    log, complete = RequestLogV01.read(path.read_bytes())
    assert complete
    assert [r.id for r in log.requests] == ["r0"]
    with pytest.raises(FileExistsError):
        LocalFileSink(path)
