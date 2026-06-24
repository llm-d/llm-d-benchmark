"""Tests for the smoketest's ``_wait_for_model_ready`` and the priority
chain used to pick its timeout / poll interval.

Pins three behaviour contracts:

1. The class default is **30 minutes**, not the historical 5 minutes
   (which is too short for any modern large model -- DeepSeek-R1 alone
   takes 15+ minutes to download + load).
2. Per-scenario override via ``harness.smoketest.modelReadyTimeout`` /
   ``modelReadyPollInterval`` in the plan config takes precedence over
   the default but loses to an explicit kwarg.
3. On timeout the helper **returns an error string** instead of silently
   logging a warning and proceeding. Historically the silent-proceed
   path let the subsequent service assertion fail with a cryptic Envoy
   "upstream connection refused" that buried the real cause (the model
   was still loading) under transport-layer noise.

Both copies of the helper live in:
- ``llmdbenchmark.smoketests.base.BaseSmoketest``
- ``llmdbenchmark.standup.steps.step_10_smoketest.SmoketestStep``

We test both to keep them in sync.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

# `llmdbenchmark.standup.steps.__init__` eagerly imports
# step_03_workload_monitoring, which depends on an external `planner`
# package not installed in this test env. We don't exercise capacity
# planning here, so stub it.
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    # Module-level __getattr__ catches any name lookup (PEP 562), so we
    # don't have to enumerate the planner exports capacity_validator.py
    # uses -- future additions stay covered without test churn.
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.smoketests.base import BaseSmoketest  # noqa: E402
from llmdbenchmark.standup.steps.step_10_smoketest import SmoketestStep  # noqa: E402


# ---------------------------------------------------------------------------
# _resolve_wait_setting -- priority chain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [BaseSmoketest, SmoketestStep])
class TestResolveWaitSetting:
    def test_default_when_nothing_set(self, cls):
        assert cls._resolve_wait_setting(None, "modelReadyTimeout", None, 1800) == 1800

    def test_explicit_kwarg_wins_over_everything(self, cls):
        plan_config = {
            "harness": {"smoketest": {"modelReadyTimeout": 9999}},
        }
        assert (
            cls._resolve_wait_setting(plan_config, "modelReadyTimeout", 42, 1800) == 42
        )

    def test_plan_config_overrides_default(self, cls):
        plan_config = {
            "harness": {"smoketest": {"modelReadyTimeout": 3600}},
        }
        assert (
            cls._resolve_wait_setting(plan_config, "modelReadyTimeout", None, 1800)
            == 3600
        )

    @pytest.mark.parametrize(
        "bad_value",
        [
            "not-an-int",
            -1,
            0,
            None,
            [],
            {"unexpected": "shape"},
        ],
    )
    def test_invalid_plan_config_value_falls_back_to_default(self, cls, bad_value):
        """Typo'd values shouldn't crash the smoketest; just use the default."""
        plan_config = {
            "harness": {"smoketest": {"modelReadyTimeout": bad_value}},
        }
        assert (
            cls._resolve_wait_setting(plan_config, "modelReadyTimeout", None, 1800)
            == 1800
        )

    def test_missing_harness_block_is_safe(self, cls):
        assert cls._resolve_wait_setting({}, "modelReadyTimeout", None, 1800) == 1800

    def test_missing_smoketest_subblock_is_safe(self, cls):
        plan_config = {"harness": {"name": "inference-perf"}}
        assert (
            cls._resolve_wait_setting(plan_config, "modelReadyTimeout", None, 1800)
            == 1800
        )

    def test_string_int_value_is_coerced(self, cls):
        """Scenarios written with YAML quoted numbers ('1800') should still work."""
        plan_config = {
            "harness": {"smoketest": {"modelReadyTimeout": "1800"}},
        }
        assert (
            cls._resolve_wait_setting(plan_config, "modelReadyTimeout", None, 300)
            == 1800
        )


# ---------------------------------------------------------------------------
# Default-bump pin
# ---------------------------------------------------------------------------


class TestDefaultsPinned:
    """If someone reverts the default to 300 we want a loud failure --
    that value was insufficient for any modern large model and produced
    misleading 'connection refused' smoketest failures."""

    def test_base_smoketest_default_is_30min(self):
        assert BaseSmoketest._DEFAULT_MODEL_READY_TIMEOUT == 1800

    def test_step_10_smoketest_default_is_30min(self):
        assert SmoketestStep._DEFAULT_MODEL_READY_TIMEOUT == 1800

    def test_poll_interval_default_is_15s(self):
        """15s gives ~5 polls/min, balancing responsiveness vs log noise."""
        assert BaseSmoketest._DEFAULT_MODEL_READY_POLL_INTERVAL == 15
        assert SmoketestStep._DEFAULT_MODEL_READY_POLL_INTERVAL == 15

    def test_base_and_step_defaults_match(self):
        """The two implementations must agree so users don't have to learn
        two different defaults."""
        assert (
            BaseSmoketest._DEFAULT_MODEL_READY_TIMEOUT
            == SmoketestStep._DEFAULT_MODEL_READY_TIMEOUT
        )
        assert (
            BaseSmoketest._DEFAULT_MODEL_READY_POLL_INTERVAL
            == SmoketestStep._DEFAULT_MODEL_READY_POLL_INTERVAL
        )


# ---------------------------------------------------------------------------
# Timeout behavior -- returns error message, not silent proceed
# ---------------------------------------------------------------------------


class TestTimeoutReturnsError:
    """Pin that timeout returns a human-actionable error string rather
    than logging a warning and silently letting the caller proceed to a
    cryptic downstream assertion failure."""

    def _build(self, cls):
        inst = cls.__new__(cls)
        return inst

    def _mocks(self):
        logger = MagicMock()
        cmd = MagicMock()
        cmd.dry_run = False
        ctx = MagicMock()
        ctx.logger = logger
        return cmd, ctx, logger

    @pytest.mark.parametrize("cls", [BaseSmoketest, SmoketestStep])
    def test_dry_run_returns_none(self, cls, monkeypatch):
        """Dry-run shouldn't poll; it returns None immediately."""
        cmd, ctx, _ = self._mocks()
        cmd.dry_run = True
        monkeypatch.setattr(
            "llmdbenchmark.smoketests.base.test_model_serving",
            lambda *a, **kw: "should not be reached",
        )
        monkeypatch.setattr(
            "llmdbenchmark.standup.steps.step_10_smoketest.test_model_serving",
            lambda *a, **kw: "should not be reached",
        )
        inst = self._build(cls)
        result = inst._wait_for_model_ready(
            cmd,
            ctx,
            "ns",
            "svc",
            80,
            "m",
            plan_config=None,
            timeout=1,
        )
        assert result is None

    @pytest.mark.parametrize(
        "cls,module",
        [
            (BaseSmoketest, "llmdbenchmark.smoketests.base"),
            (SmoketestStep, "llmdbenchmark.standup.steps.step_10_smoketest"),
        ],
    )
    def test_immediate_success_returns_none(self, cls, module, monkeypatch):
        cmd, ctx, _ = self._mocks()
        monkeypatch.setattr(
            f"{module}.test_model_serving",
            lambda *a, **kw: None,  # Success on first poll
        )
        inst = self._build(cls)
        result = inst._wait_for_model_ready(
            cmd,
            ctx,
            "ns",
            "svc",
            80,
            "m",
            plan_config=None,
            timeout=10,
            poll_interval=1,
        )
        assert result is None

    @pytest.mark.parametrize(
        "cls,module",
        [
            (BaseSmoketest, "llmdbenchmark.smoketests.base"),
            (SmoketestStep, "llmdbenchmark.standup.steps.step_10_smoketest"),
        ],
    )
    def test_timeout_returns_actionable_error(self, cls, module, monkeypatch):
        """The returned string must name the model, host, timeout, AND
        the override knob -- those are the four pieces a debugging
        user needs to fix the situation themselves."""
        cmd, ctx, logger = self._mocks()
        monkeypatch.setattr(
            f"{module}.test_model_serving",
            lambda *a, **kw: "still loading",
        )
        # Patch time.time to fast-forward past the timeout immediately on
        # the second loop iteration.
        clock = [0.0]

        def _tick():
            v = clock[0]
            clock[0] += 5  # Each call advances 5s
            return v

        monkeypatch.setattr(f"{module}.time.time", _tick)
        monkeypatch.setattr(f"{module}.time.sleep", lambda _: None)

        inst = self._build(cls)
        err = inst._wait_for_model_ready(
            cmd,
            ctx,
            "vezio-gpu",
            "svc",
            80,
            "deepseek-ai/DeepSeek-R1-0528",
            plan_config=None,
            timeout=3,
            poll_interval=1,
        )
        assert err is not None
        # Names the model
        assert "deepseek-ai/DeepSeek-R1-0528" in err
        # Names the namespace (for the kubectl command)
        assert "vezio-gpu" in err
        # Names the timeout value
        assert "3s" in err
        # Names the override knob
        assert "modelReadyTimeout" in err
        # Suggests checking model-server logs
        assert "kubectl logs" in err
        assert "vllm" in err
        # And the helper logged the error (so it's visible even if caller
        # ignores the return value).
        logger.log_error.assert_called_once()
