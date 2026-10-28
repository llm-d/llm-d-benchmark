"""The artifact base class, the kind registry and load_artifact() (#2097)."""

import importlib.resources
import json
from typing import Any, ClassVar, Literal

import pytest
import yaml
from pydantic import ValidationError

from llmd_benchmark_report import load_benchmark_report, make_json_schema
from llmd_benchmark_report.artifacts import (
    LEGACY_BENCHMARK_REPORT_VERSIONS,
    Artifact,
    ArtifactHeader,
    ValidationIssue,
    artifact_model,
    load_artifact,
    register,
)
from llmd_benchmark_report.artifacts.benchmark_report.v0_1.schema import (
    BenchmarkReportV01,
)
from llmd_benchmark_report.artifacts.benchmark_report.v0_2.schema import (
    BenchmarkReportV02,
)

PACKAGE = importlib.resources.files("llmd_benchmark_report")


# A kind registered once for this module: "sample-kind" at version "0.1" with one
# body field, points. validate() flags an empty points list under rule "sample-kind.points".
@register
class SampleKind(Artifact):
    VERSIONS: ClassVar[tuple[str, ...]] = ("0.1",)

    kind: Literal["sample-kind"]
    points: list[int]

    def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
        if self.points:
            return []
        return [
            ValidationIssue(rule="sample-kind.points", path="points", message="empty")
        ]


SAMPLE = {"kind": "sample-kind", "version": "0.1", "run": {"uid": "s1"}, "points": [1]}


# Reads a packaged example report, e.g. br_v0_2_example.yaml, as a dict.
def _example(name: str) -> dict[str, Any]:
    return yaml.safe_load((PACKAGE / name).read_text())


# The v0.2 example with its kind line removed, as every report written before kind existed.
def _legacy_v0_2() -> dict[str, Any]:
    data = _example("br_v0_2_example.yaml")
    del data["kind"]
    return data


# {"kind": "sample-kind", "version": "0.1", ...} loads as SampleKind with points [1],
# and the registry names SampleKind as the model for ("sample-kind", "0.1").
def test_registered_kind_loads_by_its_header() -> None:
    artifact = load_artifact(SAMPLE)

    assert type(artifact) is SampleKind
    assert artifact.points == [1]
    assert artifact_model("sample-kind", "0.1") is SampleKind


# points [1] passes validate(). points [] returns one issue, rule
# "sample-kind.points" at path "points".
def test_validate_returns_one_issue_per_broken_rule() -> None:
    assert load_artifact(SAMPLE).validate() == []
    broken = load_artifact({**SAMPLE, "points": []})

    assert broken.validate() == [
        ValidationIssue(rule="sample-kind.points", path="points", message="empty")
    ]


# A concrete kind that leaves kind as str, or narrows it to two names, fails the
# moment the class is defined.
def test_concrete_kind_must_name_itself() -> None:
    with pytest.raises(TypeError, match="must name its kind"):

        class Unnamed(Artifact):
            VERSIONS: ClassVar[tuple[str, ...]] = ("0.1",)

            def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
                return []

    with pytest.raises(TypeError, match="must name its kind"):

        class TwoNames(Artifact):
            VERSIONS: ClassVar[tuple[str, ...]] = ("0.1",)
            kind: Literal["a", "b"]

            def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
                return []


# kind: Literal["defaulted"] = "defaulted" fails at definition, since a default
# would write the name for a document that never stated it.
def test_kind_name_has_no_default() -> None:
    with pytest.raises(TypeError, match="must have no default"):

        class Defaulted(Artifact):
            VERSIONS: ClassVar[tuple[str, ...]] = ("0.1",)
            kind: Literal["defaulted"] = "defaulted"

            def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
                return []


# A concrete kind with an empty VERSIONS fails at definition.
def test_concrete_kind_lists_its_versions() -> None:
    with pytest.raises(TypeError, match="VERSIONS"):

        class Unversioned(Artifact):
            kind: Literal["unversioned"]

            def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
                return []


# A family base without validate() may leave kind unnamed and cannot be instantiated
# or registered. Its subclass that names kind and implements validate() builds.
def test_abstract_base_leaves_naming_to_its_subclasses() -> None:
    class Family(Artifact):
        VERSIONS: ClassVar[tuple[str, ...]] = ("0.1",)

    with pytest.raises(TypeError, match="abstract"):
        Family(kind="x", version="0.1", run={"uid": "u"})
    with pytest.raises(TypeError, match="abstract"):
        register(Family)

    class Member(Family):
        kind: Literal["family-member"]

        def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
            return []

    member = Member(kind="family-member", version="0.1", run={"uid": "m"})
    assert member.kind_name() == "family-member"


# A second class claiming ("sample-kind", "0.1") is refused and names SampleKind.
# Registering SampleKind itself again changes nothing.
def test_one_model_per_kind_and_version() -> None:
    class Impostor(Artifact):
        VERSIONS: ClassVar[tuple[str, ...]] = ("0.1",)
        kind: Literal["sample-kind"]

        def validate(self) -> list[ValidationIssue]:  # type: ignore[override]
            return []

    with pytest.raises(ValueError, match="already read by SampleKind"):
        register(Impostor)
    assert register(SampleKind) is SampleKind
    assert artifact_model("sample-kind", "0.1") is SampleKind


# kind "nope" -> "Unknown artifact kind 'nope'". sample-kind at version "9" ->
# "No sample-kind model reads version '9'. Known: 0.1".
def test_unknown_kind_or_version_says_what_is_known() -> None:
    with pytest.raises(ValueError, match="Unknown artifact kind 'nope'"):
        load_artifact({**SAMPLE, "kind": "nope"})
    with pytest.raises(ValueError, match=r"version '9'\. Known: 0\.1"):
        load_artifact({**SAMPLE, "version": "9"})


# Only the three Benchmark Report versions written before kind existed may omit it.
def test_legacy_versions_are_the_three_report_versions() -> None:
    assert LEGACY_BENCHMARK_REPORT_VERSIONS == {"0.1", "0.2", "0.2.1"}


# No kind at 0.2 or 0.2.1 -> BenchmarkReportV02 with kind "benchmark-report" filled.
# No kind at 0.1 -> BenchmarkReportV01, which has no kind field at all.
@pytest.mark.parametrize("version", ["0.2", "0.2.1"])
def test_no_kind_at_a_legacy_version_loads_as_a_report(version: str) -> None:
    report = load_artifact({**_legacy_v0_2(), "version": version})

    assert type(report) is BenchmarkReportV02
    assert report.kind == "benchmark-report"
    assert report.version == version
    assert type(load_artifact(_example("br_v0_1_example.yaml"))) is BenchmarkReportV01


# No kind at version "0.3", or with no version at all, is rejected and names the
# missing kind field.
@pytest.mark.parametrize("version", ["0.3", None])
def test_no_kind_at_any_other_version_is_rejected(version: str | None) -> None:
    with pytest.raises(ValueError, match="No `kind` field. Only Benchmark Report"):
        load_artifact({"version": version, "run": {"uid": "u"}})


# A sample-kind document that forgets kind sits at 0.1, a legacy report version, so
# it reads as a BR 0.1 and fails. The error leads with the missing kind field.
def test_new_kind_missing_its_name_fails_on_the_missing_field() -> None:
    forgot = {k: v for k, v in SAMPLE.items() if k != "kind"}

    with pytest.raises(
        ValueError, match="No `kind` field. Read as legacy benchmark-report 0.1"
    ):
        load_artifact(forgot)


# The v0.2 example with kind "sample-kind" fails as a report.
def test_report_rejects_another_kind() -> None:
    with pytest.raises(ValidationError):
        BenchmarkReportV02.model_validate(
            {**_example("br_v0_2_example.yaml"), "kind": "sample-kind"}
        )


# inference-perf validates its kind-less 0.2.1 partials with model_validate directly.
# That still passes, and the loaded report names its kind and dumps it.
def test_direct_validation_of_a_report_without_kind() -> None:
    report = BenchmarkReportV02.model_validate(_legacy_v0_2())

    assert report.kind == "benchmark-report"
    assert report.dump()["kind"] == "benchmark-report"


# {"version": "0.2.2", "run": {"uid": "u"}, "results": {}} with no kind fails, so
# the next report version will have to name its kind.
def test_report_at_a_newer_version_must_name_its_kind() -> None:
    with pytest.raises(ValidationError, match="kind"):
        BenchmarkReportV02.model_validate(
            {"version": "0.2.2", "run": {"uid": "u"}, "results": {}}
        )


# The registry has BenchmarkReportV02 for benchmark-report 0.2 and 0.2.1 and nothing
# for 0.1, whose documents never carry kind. Its validate() returns no issues.
def test_report_is_registered_for_the_0_2_line() -> None:
    assert artifact_model("benchmark-report", "0.2") is BenchmarkReportV02
    assert artifact_model("benchmark-report", "0.2.1") is BenchmarkReportV02
    with pytest.raises(ValueError, match="version '0.1'"):
        artifact_model("benchmark-report", "0.1")
    assert BenchmarkReportV02.model_validate(_legacy_v0_2()).validate() == []


# The v0.2 example with and without its kind line, and the v0.1 example, dump the
# same through load_artifact() and load_benchmark_report().
def test_both_loaders_agree_on_the_examples() -> None:
    for data in (
        _example("br_v0_2_example.yaml"),
        _legacy_v0_2(),
        _example("br_v0_1_example.yaml"),
    ):
        assert load_artifact(data).dump() == load_benchmark_report(data).dump()


# The published v0.2 schema allows kind "benchmark-report" only and does not require
# it, so reports written before kind existed still validate against it.
def test_json_schema_accepts_reports_without_kind() -> None:
    schema = json.loads(make_json_schema("0.2"))

    assert schema["properties"]["kind"]["const"] == "benchmark-report"
    assert "kind" not in schema["required"]


# The header reads kind, version and run.uid off a sample-kind document and off the
# v0.2 example, ignoring their bodies. A report without kind reads kind None.
def test_header_reads_any_kind_without_its_body() -> None:
    sample = ArtifactHeader.model_validate(SAMPLE)
    report = ArtifactHeader.model_validate(_example("br_v0_2_example.yaml"))

    assert (sample.kind, sample.version, sample.run.uid) == ("sample-kind", "0.1", "s1")
    assert (report.kind, report.version) == ("benchmark-report", "0.2.1")
    assert ArtifactHeader.model_validate(_legacy_v0_2()).kind is None
