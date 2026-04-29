"""Tests for ``VersionResolver._resolve_image_override`` and the
init-container resolution flow in ``resolve_all``.

These tests stub the registry resolution so they don't hit the network.
"""

from __future__ import annotations

from typing import Any

import pytest

from llmdbenchmark.parser.version_resolver import (
    ImageOverrideConfigError,
    VersionResolver,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubLogger:
    """Minimal logger that captures messages for assertions."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def log_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def log_info(self, msg: str) -> None:
        self.infos.append(msg)


def _make_resolver(
    monkeypatch: pytest.MonkeyPatch, *, fail: bool = False
) -> VersionResolver:
    """Build a resolver whose registry lookups are stubbed.

    ``fail=True`` makes ``resolve_image_tag`` raise, simulating an offline
    environment where skopeo/crane/podman are unreachable.
    """
    logger = _StubLogger()
    resolver = VersionResolver(logger)

    def _stub_tag(_self: Any, _registry: str, _repo: str) -> str:
        if fail:
            raise RuntimeError("simulated registry resolution failure")
        return "latest-stub"

    monkeypatch.setattr(VersionResolver, "resolve_image_tag", _stub_tag)
    return resolver


def _images() -> dict:
    """A minimal images.* block exercising both pinned and auto tags."""
    return {
        "benchmark": {
            "repository": "ghcr.io/llm-d/llm-d-benchmark",
            "tag": "auto",
            "pullPolicy": "Always",
        },
        "udsTokenizer": {
            "repository": "ghcr.io/llm-d/llm-d-uds-tokenizer",
            "tag": "v0.7.1",
            "pullPolicy": "IfNotPresent",
        },
        "broken": {
            "repository": "",
            "tag": "v1",
        },
    }


# ---------------------------------------------------------------------------
# imageKey expansion
# ---------------------------------------------------------------------------


class TestImageKeyExpansion:
    def test_explicit_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "udsTokenizer"}
        resolver._resolve_image_override(owner, _images(), "test")
        assert owner["image"] == "ghcr.io/llm-d/llm-d-uds-tokenizer:v0.7.1"
        assert owner["imagePullPolicy"] == "IfNotPresent"
        assert "imageKey" not in owner

    def test_auto_tag_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "benchmark"}
        resolver._resolve_image_override(owner, _images(), "test")
        assert owner["image"] == "ghcr.io/llm-d/llm-d-benchmark:latest-stub"
        assert owner["imagePullPolicy"] == "Always"
        assert "imageKey" not in owner

    def test_explicit_pullpolicy_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "udsTokenizer", "imagePullPolicy": "Never"}
        resolver._resolve_image_override(owner, _images(), "test")
        assert owner["imagePullPolicy"] == "Never"


# ---------------------------------------------------------------------------
# image: <full-string> backward compatibility
# ---------------------------------------------------------------------------


class TestImageStringBackcompat:
    def test_static_image_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"image": "ghcr.io/foo/bar:v1.2.3"}
        resolver._resolve_image_override(owner, _images(), "test")
        assert owner["image"] == "ghcr.io/foo/bar:v1.2.3"

    def test_auto_tag_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"image": "ghcr.io/foo/bar:auto"}
        resolver._resolve_image_override(owner, _images(), "test")
        assert owner["image"] == "ghcr.io/foo/bar:latest-stub"

    def test_no_image_no_imagekey_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        owner: dict = {"name": "preprocess"}
        resolver._resolve_image_override(owner, _images(), "test")
        assert "image" not in owner
        assert "imageKey" not in owner


# ---------------------------------------------------------------------------
# Config errors
# ---------------------------------------------------------------------------


class TestConfigErrors:
    def test_both_image_and_imagekey(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"image": "ghcr.io/foo:v1", "imageKey": "benchmark"}
        with pytest.raises(ImageOverrideConfigError, match="cannot set both"):
            resolver._resolve_image_override(owner, _images(), "test")

    def test_unknown_imagekey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "doesnotexist"}
        with pytest.raises(
            ImageOverrideConfigError, match="does not match any entry"
        ):
            resolver._resolve_image_override(owner, _images(), "test")

    def test_imagekey_to_entry_with_empty_repo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "broken"}
        with pytest.raises(
            ImageOverrideConfigError, match="empty repository or tag"
        ):
            resolver._resolve_image_override(owner, _images(), "test")

    def test_non_string_imagekey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": 42}
        with pytest.raises(ImageOverrideConfigError, match="must be a string"):
            resolver._resolve_image_override(owner, _images(), "test")


# ---------------------------------------------------------------------------
# resolve_all integration: init containers across decode / prefill / standalone
# ---------------------------------------------------------------------------


def _base_values() -> dict:
    return {
        "images": _images(),
        "decode": {
            "initContainers": [
                {"name": "preprocess", "imageKey": "benchmark"},
            ],
        },
        "prefill": {
            "initContainers": [
                {
                    "name": "preprocess",
                    "image": "ghcr.io/llm-d/llm-d-benchmark:auto",
                },
            ],
        },
        "standalone": {
            "initContainers": [
                {"name": "noop"},  # neither image nor imageKey -> template fills
            ],
        },
    }


class TestResolveAll:
    def test_init_container_imagekey_resolves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        result = resolver.resolve_all(_base_values())
        decode_ic = result["decode"]["initContainers"][0]
        assert decode_ic["image"] == "ghcr.io/llm-d/llm-d-benchmark:latest-stub"
        assert "imageKey" not in decode_ic

    def test_init_container_image_string_with_auto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        result = resolver.resolve_all(_base_values())
        prefill_ic = result["prefill"]["initContainers"][0]
        assert prefill_ic["image"] == "ghcr.io/llm-d/llm-d-benchmark:latest-stub"

    def test_init_container_no_image_left_empty_for_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty init containers stay empty -- the template fills the default."""
        resolver = _make_resolver(monkeypatch)
        result = resolver.resolve_all(_base_values())
        standalone_ic = result["standalone"]["initContainers"][0]
        assert "image" not in standalone_ic


class TestResolveAllErrors:
    def test_init_container_unknown_imagekey_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config errors in init containers must abort plan generation."""
        resolver = _make_resolver(monkeypatch)
        values = _base_values()
        values["decode"]["initContainers"][0]["imageKey"] = "doesnotexist"
        with pytest.raises(
            ImageOverrideConfigError, match="decode.initContainers"
        ):
            resolver.resolve_all(values)

    def test_init_container_both_fields_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        values = _base_values()
        values["decode"]["initContainers"][0]["image"] = "ghcr.io/foo:v1"
        # imageKey: "benchmark" already set in _base_values
        with pytest.raises(
            ImageOverrideConfigError, match="cannot set both"
        ):
            resolver.resolve_all(values)

    def test_init_container_resolution_failure_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Network/registry failures on init containers degrade to warnings."""
        resolver = _make_resolver(monkeypatch, fail=True)
        values = _base_values()
        # Drop the imageKey in decode (since failing resolution on auto tag
        # in images.benchmark would also raise during _resolve_image_tags).
        # Use a static repo:auto to isolate the init container path.
        values["decode"]["initContainers"][0] = {
            "name": "preprocess",
            "image": "ghcr.io/foo/bar:auto",
        }
        # Tag-resolution stub fails; init-container resolver should warn,
        # not raise.
        result = resolver.resolve_all(values)
        assert any(
            "Could not resolve" in w
            for w in resolver.logger.warnings
        ), f"Expected resolution warning, got: {resolver.logger.warnings}"
        # The image should remain unchanged (still :auto)
        decode_ic = result["decode"]["initContainers"][0]
        assert decode_ic["image"] == "ghcr.io/foo/bar:auto"
