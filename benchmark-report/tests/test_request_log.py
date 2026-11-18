"""request-log 0.1: records and the model built from a stream."""

import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from llmd_benchmark_report.artifacts import (
    LocalFileSink,
    Run,
    artifact_model,
    load_artifact,
)
from llmd_benchmark_report.artifacts.request_log.v0_1.schema import (
    RequestLogV01,
    RequestRecord,
    TokenCount,
    TokenSource,
)

T0 = datetime.datetime(2026, 11, 17, 10, 0, tzinfo=datetime.UTC)


# Two requests, one with token counts from the server and one that timed out,
# read back equal, complete, registered, and with no validate() issues.
def test_round_trip(tmp_path: Path) -> None:
    ok = RequestRecord(
        id="r0",
        stage=0,
        scheduled=T0,
        sent=T0 + datetime.timedelta(milliseconds=1),
        first_token=T0 + datetime.timedelta(milliseconds=80),
        end=T0 + datetime.timedelta(seconds=4),
        input_tokens=TokenCount(count=1000, source=TokenSource.SERVER),
        output_tokens=TokenCount(count=128, source=TokenSource.SERVER),
        prompt_sha256="ab" * 32,
    )
    failed = RequestRecord(id="r1", sent=T0, error="timeout")
    path = tmp_path / "requests.jsonl.gz"
    with RequestLogV01.open(LocalFileSink(path), Run(uid="r1")) as writer:
        writer.write(ok)
        writer.write(failed)
    log, complete = RequestLogV01.read(path.read_bytes())
    assert complete
    assert log.requests == [ok, failed]
    assert log.validate() == []
    assert artifact_model("request-log", "0.1") is RequestLogV01
    assert load_artifact(log.model_dump(mode="json")) == log


# A send time with no zone is refused.
def test_a_naive_time_is_refused() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        RequestRecord(id="r0", sent=datetime.datetime(2026, 11, 17, 10, 0))
