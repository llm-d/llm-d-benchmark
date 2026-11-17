"""metric-time-series 0.1: every scraped series at full resolution, per pod.

Stub. The encoding and the shared stream are real. The rules #2107 lists are
not checked yet, see validate().
"""

from typing import Any, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel

from ...base import MODEL_CONFIG, ValidationIssue
from ...registry import register
from ...stream import StreamingArtifact
from .codec import Scrape, ScrapeDecoder, ScrapeEncoder, SeriesKey, SeriesType

VERSION = "0.1"


class Point(BaseModel):
    """One value of a series."""

    model_config = MODEL_CONFIG.copy()

    ts: AwareDatetime
    """When it was scraped. A timestamp without a zone is rejected."""
    value: float


class Series(BaseModel):
    """One Prometheus series from one pod, unclipped."""

    model_config = MODEL_CONFIG.copy()

    name: str
    """Raw Prometheus name, so a new metric needs no schema change."""
    labels: dict[str, str] = {}
    pod: str | None = None
    role: str | None = None
    type: SeriesType
    points: list[Point]


@register
class MetricTimeSeriesV01(StreamingArtifact):
    """Scraped series for one run, written one scrape at a time."""

    model_config = MODEL_CONFIG.copy()
    model_config["title"] = f"Metric Time Series v{VERSION}"

    VERSIONS: ClassVar[tuple[str, ...]] = (VERSION,)

    kind: Literal["metric-time-series"]
    version: str = VERSION
    series: list[Series] = []

    @classmethod
    def encoder(cls) -> ScrapeEncoder:
        return ScrapeEncoder()

    @classmethod
    def decoder(cls) -> ScrapeDecoder:
        return ScrapeDecoder()

    @classmethod
    def from_records(cls, header: dict[str, Any], records: list[Any]) -> Self:
        points: dict[SeriesKey, list[Point]] = {}
        scrapes: list[Scrape] = records
        for scrape in scrapes:
            for key, value in scrape.values.items():
                points.setdefault(key, []).append(Point(ts=scrape.ts, value=value))
        series = [
            Series(
                name=key.name,
                labels=dict(key.labels),
                pod=key.pod,
                role=key.role,
                type=key.type,
                points=pts,
            )
            for key, pts in points.items()
        ]
        return cls.model_validate({**header, "series": series})

    def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
        # TODO(#2107): timestamps rise within each series, and the report's
        # 256-point embed equals series_points(clip_to_window(series), 256)
        return []
