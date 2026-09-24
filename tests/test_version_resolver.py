"""Tests for ``VersionResolver._resolve_image_override`` and the
init-container resolution flow in ``resolve_all``.

These tests stub the registry resolution so they don't hit the network.
"""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import Any

import pytest
import requests

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
    environment where the registry and podman are unreachable.
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
            "tag": "v0.8.0",
            "pullPolicy": "IfNotPresent",
        },
        "broken": {
            "repository": "",
            "tag": "v1",
        },
    }


# ---------------------------------------------------------------------------
# Registry tag ordering
# ---------------------------------------------------------------------------


class _StubResponse:
    """Enough of a ``requests`` response for the two-step registry handshake."""

    def __init__(
        self,
        payload: Any = None,
        status_code: int = 200,
        headers: dict | None = None,
        next_url: str | None = None,
    ) -> None:
        self.status_code = status_code
        # Real responses match a header whatever its casing; a plain dict would
        # only match the spelling a test happens to use.
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.links = {"next": {"url": next_url}} if next_url else {}
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class _StubSession:
    """Answers each GET from *routes*, recording the calls for assertions."""

    def __init__(self, routes: list[_StubResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs: Any) -> _StubResponse:
        self.calls.append((url, kwargs))
        index = len(self.calls) - 1
        assert index < len(self.routes), f"unexpected extra GET: {url}"
        return self.routes[index]

    def __enter__(self) -> "_StubSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _stub_session(monkeypatch: pytest.MonkeyPatch, *routes: _StubResponse):
    session = _StubSession(list(routes))
    monkeypatch.setattr(
        "llmdbenchmark.parser.version_resolver.requests.Session", lambda: session
    )
    return session


class TestRegistryEndpoint:
    @pytest.mark.parametrize(
        ("image_ref", "expected"),
        [
            ("quay.io/aruocco/bench", ("quay.io", "aruocco/bench")),
            ("ghcr.io/llm-d/router", ("ghcr.io", "llm-d/router")),
            # A bare name is Docker Hub, and its official images live under library/.
            ("redis", ("registry-1.docker.io", "library/redis")),
            ("vllm/vllm-openai", ("registry-1.docker.io", "vllm/vllm-openai")),
            # The pull alias is not the API host.
            (
                "docker.io/vllm/vllm-openai",
                ("registry-1.docker.io", "vllm/vllm-openai"),
            ),
            ("localhost:5000/bench", ("localhost:5000", "bench")),
            ("registry:5000/bench", ("registry:5000", "bench")),
        ],
    )
    def test_host_and_repo_split(self, image_ref: str, expected: tuple) -> None:
        assert VersionResolver._registry_endpoint(image_ref) == expected


class TestRegistryTagOrdering:
    def test_selects_latest_tag_by_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_session(
            monkeypatch,
            _StubResponse({"tags": ["v0.20.1", "v0.9.2", "v0.10.0"]}),
        )
        resolver = VersionResolver(_StubLogger())

        assert resolver._resolve_via_registry("quay.io/vllm/vllm-openai") == "v0.20.1"

    def test_a_token_is_reused_across_pages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _stub_session(
            monkeypatch,
            _StubResponse(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="https://ghcr.io/token"'},
            ),
            _StubResponse({"token": "t0ken"}),
            _StubResponse({"tags": ["v0.1.0"]}, next_url="/v2/a/b/tags/list"),
            _StubResponse({"tags": ["v0.3.0"]}),
        )
        resolver = VersionResolver(_StubLogger())

        assert resolver._resolve_via_registry("ghcr.io/a/b") == "v0.3.0"
        assert session.calls[-1][1]["headers"]["Authorization"] == "Bearer t0ken"

    def test_a_challenge_is_answered_with_a_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _stub_session(
            monkeypatch,
            _StubResponse(
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Bearer realm="https://ghcr.io/token",'
                    'service="ghcr.io"'
                },
            ),
            _StubResponse({"token": "t0ken"}),
            _StubResponse({"tags": ["v1.0.0", "v2.0.0"]}),
        )
        resolver = VersionResolver(_StubLogger())

        assert resolver._resolve_via_registry("ghcr.io/llm-d/router") == "v2.0.0"
        token_url, token_kwargs = session.calls[1]
        assert token_url == "https://ghcr.io/token"
        assert token_kwargs["params"]["scope"] == "repository:llm-d/router:pull"
        assert session.calls[2][1]["headers"]["Authorization"] == "Bearer t0ken"

    @pytest.mark.parametrize(
        "routes",
        [
            # Unreachable, unparsable, and a challenge with no realm to answer:
            # each must degrade to the podman fallback, never raise.
            (_StubResponse(status_code=404),),
            (_StubResponse(None),),
            (_StubResponse(status_code=401, headers={"www-authenticate": "Bearer"}),),
        ],
    )
    def test_an_unreadable_registry_resolves_to_nothing(
        self, monkeypatch: pytest.MonkeyPatch, routes: tuple
    ) -> None:
        _stub_session(monkeypatch, *routes)
        resolver = VersionResolver(_StubLogger())

        assert resolver._resolve_via_registry("quay.io/x/y") is None

    def test_a_network_error_resolves_to_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Boom(_StubSession):
            def get(self, url: str, **kwargs: Any) -> _StubResponse:
                raise requests.ConnectionError("offline")

        monkeypatch.setattr(
            "llmdbenchmark.parser.version_resolver.requests.Session",
            lambda: _Boom([]),
        )
        resolver = VersionResolver(_StubLogger())

        assert resolver._resolve_via_registry("quay.io/x/y") is None

    def test_podman_covers_a_registry_that_cannot_be_queried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_session(monkeypatch, _StubResponse(status_code=404))

        def _run(*args: Any, **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(
                args[0], 0, stdout="NAME TAG\nquay.io/x/y v0.1.0\nquay.io/x/y v0.2.0\n"
            )

        monkeypatch.setattr(
            "llmdbenchmark.parser.version_resolver.subprocess.run", _run
        )
        resolver = VersionResolver(_StubLogger())

        assert resolver.resolve_image_tag("", "quay.io/x/y") == "v0.2.0"


# ---------------------------------------------------------------------------
# imageKey expansion
# ---------------------------------------------------------------------------


class TestImageKeyExpansion:
    def test_explicit_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "udsTokenizer"}
        resolver._resolve_image_override(owner, _images(), "test")
        assert owner["image"] == "ghcr.io/llm-d/llm-d-uds-tokenizer:v0.8.0"
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
    def test_both_image_and_imagekey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"image": "ghcr.io/foo:v1", "imageKey": "benchmark"}
        with pytest.raises(ImageOverrideConfigError, match="cannot set both"):
            resolver._resolve_image_override(owner, _images(), "test")

    def test_unknown_imagekey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "doesnotexist"}
        with pytest.raises(ImageOverrideConfigError, match="does not match any entry"):
            resolver._resolve_image_override(owner, _images(), "test")

    def test_imagekey_to_entry_with_empty_repo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        owner = {"imageKey": "broken"}
        with pytest.raises(ImageOverrideConfigError, match="empty repository or tag"):
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
        with pytest.raises(ImageOverrideConfigError, match="decode.initContainers"):
            resolver.resolve_all(values)

    def test_init_container_both_fields_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = _make_resolver(monkeypatch)
        values = _base_values()
        values["decode"]["initContainers"][0]["image"] = "ghcr.io/foo:v1"
        # imageKey: "benchmark" already set in _base_values
        with pytest.raises(ImageOverrideConfigError, match="cannot set both"):
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
        assert any("Could not resolve" in w for w in resolver.logger.warnings), (
            f"Expected resolution warning, got: {resolver.logger.warnings}"
        )
        # The image should remain unchanged (still :auto)
        decode_ic = result["decode"]["initContainers"][0]
        assert decode_ic["image"] == "ghcr.io/foo/bar:auto"


# ---------------------------------------------------------------------------
# nok8s: Kubernetes-only resolutions are skipped
# ---------------------------------------------------------------------------


def _nok8s_values() -> dict:
    """Values shaped like a rendered nok8s stack: container image plus the
    Kubernetes-only WVA image and chart versions."""
    return {
        "images": {
            "benchmark": {
                "repository": "ghcr.io/llm-d/llm-d-benchmark",
                "tag": "auto",
            },
        },
        "wva": {
            "image": {
                "repository": "ghcr.io/llm-d/llm-d-workload-variant-autoscaler",
                "tag": "auto",
            }
        },
        "chartVersions": {"llmDInfra": "auto", "llmDModelservice": "auto"},
    }


class TestSkipKubernetes:
    def test_nok8s_skips_wva_and_chart_warnings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No helm/skopeo warnings on the nok8s path: nothing consumes the
        WVA image or the chart versions there."""
        resolver = _make_resolver(monkeypatch)
        monkeypatch.setattr(
            VersionResolver,
            "resolve_chart_version",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("helm missing")),
        )

        result = resolver.resolve_all(_nok8s_values(), skip_kubernetes=True)

        assert resolver.logger.warnings == [], (
            f"Expected no warnings on the nok8s path, got: {resolver.logger.warnings}"
        )
        # Kubernetes-only values are left untouched rather than resolved.
        assert result["wva"]["image"]["tag"] == "auto"
        assert result["chartVersions"] == {
            "llmDInfra": "auto",
            "llmDModelservice": "auto",
        }
        # The container image nok8s actually runs is still resolved.
        assert result["images"]["benchmark"]["tag"] == "latest-stub"

    def test_kubernetes_path_still_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default path keeps warning, so the nok8s assertion above is
        testing the skip and not an unreachable code path."""
        resolver = _make_resolver(monkeypatch, fail=True)
        monkeypatch.setattr(
            VersionResolver,
            "resolve_chart_version",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("helm missing")),
        )

        resolver.resolve_all(_nok8s_values())

        warnings = " ".join(resolver.logger.warnings)
        assert "Could not resolve WVA image tag" in warnings
        assert "chartVersions.llmDInfra" in warnings
