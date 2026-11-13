"""Which model reads each (kind, version), and :func:`load_artifact` on top of it."""

import inspect
from typing import Any, TypeVar

from pydantic import ValidationError

from .base import Artifact
from .benchmark_report.base import BenchmarkReport

# Benchmark Report versions written before `kind` existed. Every later document
# carries `kind`, so this set never grows.
LEGACY_BENCHMARK_REPORT_VERSIONS = frozenset({"0.1", "0.2", "0.2.1"})

_MODELS: dict[tuple[str, str], type[Artifact]] = {}

A = TypeVar("A", bound=Artifact)


def register(cls: type[A]) -> type[A]:
    """Make ``cls`` the model for its kind at every version in ``cls.VERSIONS``."""
    if inspect.isabstract(cls):
        raise TypeError(f"{cls.__name__} is abstract and cannot read documents")
    kind = cls.kind_name()
    for version in cls.VERSIONS:
        taken = _MODELS.get((kind, version))
        if taken is not None and taken is not cls:
            raise ValueError(f"{kind} {version} is already read by {taken.__name__}")
    for version in cls.VERSIONS:
        _MODELS[(kind, version)] = cls
    return cls


def artifact_model(kind: str, version: str) -> type[Artifact]:
    """The registered model for ``kind`` at ``version``."""
    model = _MODELS.get((kind, version)) if isinstance(version, str) else None
    if model is not None:
        return model
    known = sorted(v for k, v in _MODELS if k == kind)
    if known:
        raise ValueError(
            f"No {kind} model reads version {version!r}. Known: {', '.join(known)}"
        )
    raise ValueError(f"Unknown artifact kind {kind!r}")


def load_artifact(data: dict[str, Any]) -> Artifact | BenchmarkReport:
    """Load an artifact document by its ``kind`` and ``version``.

    A document without ``kind`` loads only as a Benchmark Report written before
    ``kind`` existed, at one of ``LEGACY_BENCHMARK_REPORT_VERSIONS``.
    """
    kind = data.get("kind")
    version = data.get("version")
    if kind is not None:
        return artifact_model(kind, version).model_validate(data)
    if isinstance(version, str) and version in LEGACY_BENCHMARK_REPORT_VERSIONS:
        # core imports the report schemas, which import this module to register
        from ..core import load_benchmark_report

        try:
            return load_benchmark_report(data)
        except ValidationError as err:
            raise ValueError(
                f"No `kind` field. Read as legacy benchmark-report {version} "
                f"and failed:\n{err}"
            ) from err
    raise ValueError(
        f"No `kind` field. Only Benchmark Report versions "
        f"{', '.join(sorted(LEGACY_BENCHMARK_REPORT_VERSIONS))} may omit it, "
        f"and this document declares version {version!r}"
    )
