"""Tests that every harness records the same run metadata keys.

run_metadata.yaml is the only handoff from a harness pod to driver-side
analysis, and each harness writes its own copy, so an omitted key silently
loses that field from its reports.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

HARNESS_DIR = Path(__file__).resolve().parent.parent / "workload" / "harnesses"

# Not the full schema: these two have no effect until a user sets a description,
# so a new harness can omit them without anything else failing.
REQUIRED_KEYS = ("description_text", "description_keywords")

WRITERS = sorted(
    path
    for path in HARNESS_DIR.iterdir()
    if path.is_file() and "run_metadata.yaml" in path.read_text(encoding="utf-8")
)


def test_every_harness_writer_is_discovered() -> None:
    """Guards the discovery itself: a glob that matches nothing passes silently."""
    assert len(WRITERS) >= 8


@pytest.mark.parametrize("writer", WRITERS, ids=lambda path: path.name)
def test_writer_records_the_run_description(writer: Path) -> None:
    contents = writer.read_text(encoding="utf-8")

    # Not `f"{key}:"`: the shell writers emit YAML, priority_mix.py a dict key.
    for key in REQUIRED_KEYS:
        assert key in contents, f"{writer.name} does not record {key}"


@pytest.mark.parametrize("writer", WRITERS, ids=lambda path: path.name)
def test_shell_writers_escape_the_description(writer: Path) -> None:
    """Only the shell writers interpolate into the YAML by hand; priority_mix.py
    goes through yaml.safe_dump, which quotes correctly on its own."""
    if writer.suffix != ".sh":
        pytest.skip("not a shell writer")
    contents = writer.read_text(encoding="utf-8")

    assert '_yaml_escape "${LLMDBENCH_DESCRIPTION_TEXT:-}"' in contents
    # Backslash before quote: escaping in the other order double-escapes.
    assert contents.index("//\\\\/") < contents.index('//\\"/')


SHELL_WRITERS = [writer for writer in WRITERS if writer.suffix == ".sh"]

# One character from each class that has to be escaped, plus the YAML break a
# naive escaper lets through. Control characters are illegal in a double-quoted
# scalar, and both readers treat a parse failure as "no metadata at all", so one
# unescaped byte drops harness_rc, model and the timings too -- not just this
# field.
HOSTILE_DESCRIPTIONS = [
    'has "quotes", a back\\slash, a: colon and #hash',
    "perf \x1b[31mred\x1b[0m run",
    "Line one.\nLine two.\n\nPara two.",
    'x"\nharness_rc: "1',
]


def _escape_function(writer: Path) -> str:
    """Return a writer's own _yaml_escape definition."""
    body = writer.read_text(encoding="utf-8")
    start = body.index("_yaml_escape() {")
    return body[start : body.index("\n}\n", start) + len("\n}\n")]


@pytest.mark.parametrize("text", HOSTILE_DESCRIPTIONS)
def test_hostile_description_round_trips(text: str) -> None:
    """A description must survive the writer verbatim and keep the file parseable."""
    script = (
        _escape_function(HARNESS_DIR / "inference-perf-llm-d-benchmark.sh")
        + 'printf \'harness_rc: "0"\\ndescription_text: "%s"\\n\''
        + ' "$(_yaml_escape "$LLMDBENCH_DESCRIPTION_TEXT")"\n'
    )
    written = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LLMDBENCH_DESCRIPTION_TEXT": text},
    ).stdout

    parsed = yaml.safe_load(written)

    assert parsed["description_text"] == text
    assert parsed["harness_rc"] == "0"


def test_every_shell_writer_escapes_identically() -> None:
    """The behaviour above is tested once, so the copies must not drift from it."""
    definitions = {_escape_function(writer) for writer in SHELL_WRITERS}

    assert len(definitions) == 1
