"""Artifacts written one record at a time, so a run that dies still leaves what it recorded.

On disk a stream is JSON lines in gzip members. The first line is the artifact
header, then each kind's record lines, then an end line that close() writes.
Every flush is one complete gzip member, and gzip members joined end to end are
still one valid gzip file. So a stream can be appended to a local file, or
uploaded as numbered segments and joined later, and a crash loses only what was
not yet flushed.
"""

import datetime
import gzip
import json
import os
import time
import zlib
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Generic, NamedTuple, Protocol, Self, TypeVar

from .base import Artifact, ArtifactHeader, Producer, Run

# `$` keeps it apart from any record key a pydantic field name could produce
END_KEY = "$end"

R = TypeVar("R")
R_contra = TypeVar("R_contra", contravariant=True)
R_co = TypeVar("R_co", covariant=True)


class Sink(Protocol):
    """Where flushed bytes go: a local file, or numbered objects in a bucket."""

    def write_segment(self, data: bytes) -> None:
        """Store one complete gzip member after the ones already stored."""

    def close(self) -> None:
        """Called once, after the last segment."""


class LocalFileSink:
    """Appends each segment to one file and syncs it to disk."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        # exclusive create, so a new run never appends to an old run's file
        self._file = self.path.open("xb")

    def write_segment(self, data: bytes) -> None:
        self._file.write(data)
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()


class RecordEncoder(Protocol[R_contra]):
    """Turns one record into the lines that hold it. May keep state across records."""

    def encode(self, record: R_contra) -> list[dict[str, Any]]: ...


class RecordDecoder(Protocol[R_co]):
    """Reads lines back in order. Returns None for a line that only updates its state."""

    def decode(self, line: dict[str, Any]) -> R_co | None: ...


def _dumps(line: dict[str, Any]) -> str:
    # compact, and no NaN or Infinity, which JSON readers outside Python reject
    return json.dumps(line, separators=(",", ":"), allow_nan=False)


class StreamWriter(Generic[R]):
    """Buffers encoded lines and flushes them as one gzip member per flush.

    A flush happens when ``flush_lines`` lines are buffered or ``flush_seconds``
    have passed since the last flush, checked on each write(). Leaving a ``with``
    block on an exception flushes what is buffered and skips the end line, so
    readers see the stream as partial.
    """

    def __init__(
        self,
        sink: Sink,
        header: ArtifactHeader,
        encoder: RecordEncoder[R],
        *,
        flush_lines: int = 1000,
        flush_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._encoder = encoder
        self._flush_lines = flush_lines
        self._flush_seconds = flush_seconds
        self._clock = clock
        self._records = 0
        self._closed = False
        self._last_flush = clock()
        self._buffer = [_dumps(header.model_dump(mode="json", exclude_none=True))]
        # the header goes out now, so a run that dies before its first record
        # still leaves a file that says what it was
        self.flush()

    def write(self, record: R) -> None:
        if self._closed:
            raise ValueError("write() after close()")
        for line in self._encoder.encode(record):
            if END_KEY in line:
                raise ValueError(f"{END_KEY!r} is reserved for the end line")
            self._buffer.append(_dumps(line))
        self._records += 1
        if (
            len(self._buffer) >= self._flush_lines
            or self._clock() - self._last_flush >= self._flush_seconds
        ):
            self.flush()

    def flush(self) -> None:
        if self._buffer:
            text = "".join(line + "\n" for line in self._buffer)
            self._sink.write_segment(gzip.compress(text.encode(), mtime=0))
            self._buffer = []
        self._last_flush = self._clock()

    def close(self) -> None:
        """Write the end line, flush, and close the sink."""
        if self._closed:
            return
        self._buffer.append(_dumps({END_KEY: {"records": self._records}}))
        self._finish()

    def _finish(self) -> None:
        self.flush()
        self._sink.close()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()
        elif not self._closed:
            self._finish()


class StreamContents(NamedTuple):
    """A stream split into its header, its record lines and its end line."""

    header: dict[str, Any]
    lines: list[dict[str, Any]]
    end: dict[str, Any] | None
    """None when the writer never closed the stream."""


def read_stream(data: bytes) -> StreamContents:
    """Split a stream into header, record lines and end line.

    Reads one gzip member at a time and stops at the first member that is cut
    off or corrupt, keeping everything before it. Python's gzip module raises on
    a cut-off file instead.
    """
    chunks: list[bytes] = []
    rest = data
    while rest:
        member = zlib.decompressobj(wbits=31)
        try:
            out = member.decompress(rest)
        except zlib.error:
            break
        if not member.eof:
            break
        chunks.append(out)
        rest = member.unused_data
    lines = [json.loads(text) for text in b"".join(chunks).decode().splitlines()]
    if not lines:
        raise ValueError("Stream has no complete header line")
    header, body = lines[0], lines[1:]
    end = body.pop()[END_KEY] if body and END_KEY in body[-1] else None
    return StreamContents(header, body, end)


class StreamingArtifact(Artifact):
    """Base of the kinds a producer writes one record at a time.

    A kind supplies its encoder and decoder and builds its model from the
    decoded records. open() and read() are shared.
    """

    @classmethod
    @abstractmethod
    def encoder(cls) -> RecordEncoder[Any]:
        """A fresh encoder for one stream."""

    @classmethod
    @abstractmethod
    def decoder(cls) -> RecordDecoder[Any]:
        """A fresh decoder for one stream."""

    @classmethod
    @abstractmethod
    def from_records(cls, header: dict[str, Any], records: list[Any]) -> Self:
        """The in-memory artifact for a header and the records decoded after it."""

    @classmethod
    def open(
        cls,
        sink: Sink,
        run: Run,
        producer: Producer | None = None,
        *,
        flush_lines: int = 1000,
        flush_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> StreamWriter[Any]:
        """A writer for a new stream of this kind at its newest version."""
        header = ArtifactHeader(
            kind=cls.kind_name(), version=cls.VERSIONS[-1], run=run, producer=producer
        )
        return StreamWriter(
            sink,
            header,
            cls.encoder(),
            flush_lines=flush_lines,
            flush_seconds=flush_seconds,
            clock=clock,
        )

    @classmethod
    def read(cls, data: bytes) -> tuple[Self, bool]:
        """The artifact, and whether its writer closed it. False means the run stopped early."""
        contents = read_stream(data)
        kind = contents.header.get("kind")
        version = contents.header.get("version")
        if kind != cls.kind_name() or version not in cls.VERSIONS:
            raise ValueError(
                f"{cls.__name__} reads {cls.kind_name()} {', '.join(cls.VERSIONS)}, "
                f"this stream is {kind} {version}"
            )
        decoder = cls.decoder()
        records = [
            record
            for line in contents.lines
            if (record := decoder.decode(line)) is not None
        ]
        if contents.end is not None and contents.end.get("records") != len(records):
            raise ValueError(
                f"End line counts {contents.end.get('records')} records, "
                f"the stream holds {len(records)}"
            )
        return cls.from_records(contents.header, records), contents.end is not None


def require_zone(ts: datetime.datetime) -> datetime.datetime:
    """``ts`` unchanged, or ValueError when it has no zone."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"Timestamp {ts.isoformat()} has no zone")
    return ts
