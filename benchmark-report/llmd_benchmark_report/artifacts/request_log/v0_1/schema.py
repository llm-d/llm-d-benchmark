"""request-log 0.1: one record per request, written by the load generator.

Stub. Each record is one plain JSON line. Integer microsecond offsets for the
times cut a synthetic 100k-request log from 4.2 MiB to 3.4 MiB gzipped, and are
not done yet. Neither is the workload definition the producer attaches.
"""

from enum import StrEnum
from typing import Any, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, Field

from ...base import MODEL_CONFIG, ValidationIssue
from ...registry import register
from ...stream import StreamingArtifact

VERSION = "0.1"


class TokenSource(StrEnum):
    """Who counted the tokens."""

    SERVER = "server"
    """The usage block of the server's response."""
    TOKENIZER = "tokenizer"
    """The load generator's own tokenizer."""


class TokenCount(BaseModel):
    """A token count and who counted it."""

    model_config = MODEL_CONFIG.copy()

    count: int = Field(ge=0)
    source: TokenSource


class RequestRecord(BaseModel):
    """One request. No prompt or response content."""

    model_config = MODEL_CONFIG.copy()

    id: str
    stage: int | None = None
    session: str | None = None
    turn: int | None = None
    scheduled: AwareDatetime | None = None
    """When the load generator meant to send it. None when nothing schedules sends."""
    sent: AwareDatetime
    first_token: AwareDatetime | None = None
    end: AwareDatetime | None = None
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    error: str | None = None
    """Error class, e.g. ``timeout``. None when the request succeeded."""
    server_request_id: str | None = None
    prompt_sha256: str | None = None
    dataset_row: str | None = None
    chunk_times: list[AwareDatetime] | None = None
    """Arrival of each streamed chunk. Off by default."""


class RequestEncoder:
    """One plain JSON line per record."""

    def encode(self, record: RequestRecord) -> list[dict[str, Any]]:
        return [record.model_dump(mode="json", exclude_none=True)]


class RequestDecoder:
    def decode(self, line: dict[str, Any]) -> RequestRecord:
        return RequestRecord.model_validate(line)


@register
class RequestLogV01(StreamingArtifact):
    """Every request of one run, written as each one finishes."""

    model_config = MODEL_CONFIG.copy()
    model_config["title"] = f"Request Log v{VERSION}"

    VERSIONS: ClassVar[tuple[str, ...]] = (VERSION,)

    kind: Literal["request-log"]
    version: str = VERSION
    requests: list[RequestRecord] = []

    @classmethod
    def encoder(cls) -> RequestEncoder:
        return RequestEncoder()

    @classmethod
    def decoder(cls) -> RequestDecoder:
        return RequestDecoder()

    @classmethod
    def from_records(cls, header: dict[str, Any], records: list[Any]) -> Self:
        return cls.model_validate({**header, "requests": records})

    def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
        # TODO(#2107): sent >= scheduled, first_token >= sent, end >= first_token,
        # and ids unique within the run
        return []
