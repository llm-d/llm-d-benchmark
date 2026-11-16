"""The header every artifact kind shares, and the base class each kind extends."""

import datetime
import inspect
from abc import abstractmethod
from typing import Any, ClassVar, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, model_validator

# Default model_config to apply to Pydantic classes
MODEL_CONFIG = ConfigDict(
    extra="forbid",  # Do not allow fields that are not part of this schema
    use_attribute_docstrings=True,  # Use docstrings for JSON schema
    populate_by_name=False,  # Must use alias name, not internal field name
    validate_assignment=True,  # Validate field assignment after init
)


class RunTime(BaseModel):
    """Time details of experiment."""

    model_config = MODEL_CONFIG.copy()

    start: datetime.datetime | None = None
    """ISO-8601 timestamp for experiment start."""
    end: datetime.datetime | None = None
    """ISO-8601 timestamp for experiment end."""
    duration: str | None = None
    """ISO-8601 duration for experiment."""


class Run(BaseModel):
    """Benchmark run details."""

    model_config = MODEL_CONFIG.copy()

    uid: str
    """Unique ID for this specific artifact."""
    eid: str | None = None
    """Experiment ID, common across artifacts from a particular experiment."""
    cid: str | None = None
    """Cluster ID, unique to a particular cluster."""
    pid: str | None = None
    """Pod ID, unique to a workload generating and/or data collecting pod."""
    time: RunTime | None = None
    """Time details of experiment."""
    user: str | None = None
    """Username that executed experiment."""
    description: str | None = None
    """User-provided description of the experiment."""
    keywords: list[str] | None = None
    """User-provided keywords/tags for the experiment."""


class Producer(BaseModel):
    """Tool that wrote the artifact."""

    model_config = MODEL_CONFIG.copy()

    name: str
    """Tool name, e.g. llm-d-benchmark."""
    version: str
    """Tool version."""


class ValidationIssue(BaseModel):
    """One rule an artifact breaks, as ``validate()`` reports it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    """Stable rule id, shared with the fixtures that exercise the rule."""
    path: str
    """Dotted path to the offending field, e.g. ``results.request_performance``."""
    message: str
    """What is wrong, for a person to read."""


class ArtifactHeader(BaseModel):
    """The shared header read off any artifact, without validating its body."""

    model_config = ConfigDict(extra="ignore")

    # Optional so the header also reads documents written before `kind` existed.
    kind: str | None = None
    version: str | None = None
    run: Run | None = None
    producer: Producer | None = None
    derived_from: list[str] | None = None


def _drop_legacy_kind_from_required(
    schema: dict[str, Any], model: type[BaseModel]
) -> None:
    # documents at a legacy version are valid without `kind`, so the schema must accept them
    if getattr(model, "LEGACY_VERSIONS", None) and "kind" in schema.get("required", []):
        schema["required"].remove("kind")


class Artifact(BaseModel):
    """Base of every artifact kind. Subclasses add the kind-specific body.

    A concrete kind narrows ``kind`` to its one name with no default, lists the
    schema versions it reads in ``VERSIONS``, implements ``validate()`` and is
    registered with :func:`~llmd_benchmark_report.artifacts.registry.register`.
    """

    model_config = MODEL_CONFIG.copy()
    model_config["json_schema_extra"] = _drop_legacy_kind_from_required

    VERSIONS: ClassVar[tuple[str, ...]] = ()
    # Versions written before `kind` existed. A document at one of these may omit
    # `kind` and still reads as this kind. Empty for every kind but the report.
    LEGACY_VERSIONS: ClassVar[frozenset[str]] = frozenset()

    kind: str
    """Artifact kind, the key the registry looks the model up by."""
    version: str
    """Schema version of this kind."""
    run: Run
    """Run details. ``run.uid`` identifies this artifact."""
    producer: Producer | None = None
    """Tool and version that wrote the artifact."""
    derived_from: list[str] | None = None
    """``run.uid`` of each artifact this one was built from."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # an abstract intermediate may leave naming to its subclasses
        if inspect.isabstract(cls):
            return
        field = cls.model_fields["kind"]
        names = get_args(field.annotation)
        if (
            get_origin(field.annotation) is not Literal
            or len(names) != 1
            or not isinstance(names[0], str)
        ):
            raise TypeError(
                f'{cls.__name__} must name its kind, e.g. kind: Literal["metric-time-series"]'
            )
        if not field.is_required():
            raise TypeError(
                f"{cls.__name__}.kind must have no default, so every document names its kind"
            )
        if not cls.VERSIONS:
            raise TypeError(
                f"{cls.__name__} must list the schema versions it reads in VERSIONS"
            )

    @classmethod
    def kind_name(cls) -> str:
        """The one name this kind narrows ``kind`` to."""
        return get_args(cls.model_fields["kind"].annotation)[0]

    @model_validator(mode="before")
    @classmethod
    def _read_legacy_kind(cls, data: Any) -> Any:
        # Only versions that predate `kind` read without it. A new kind has none,
        # so its missing name is an error rather than a guess.
        if not isinstance(data, dict) or "kind" in data or not cls.LEGACY_VERSIONS:
            return data
        version = data.get("version", cls.model_fields["version"].default)
        if isinstance(version, str) and version in cls.LEGACY_VERSIONS:
            return {**data, "kind": cls.kind_name()}
        return data

    # BaseModel.validate is pydantic v1's deprecated classmethod, unrelated to this
    @abstractmethod
    def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
        """Check the rules the schema cannot express. Empty when the artifact passes."""
