"""Tests for the ``update`` safety gate.

Validates that:
- an update with nothing to change is refused
- an unknown --set key is refused
- a dangerous --set key, flag or --component needs --force
- a warn-only key that reaches a dangerous component needs --force too
- a stack-scoped --set outside --stack is refused
- --cluster-config keys do not decide the scope, nor refuse the run
- -s/--step overrides the inferred steps, and answers to --force too
"""

from __future__ import annotations

import argparse

import pytest

from llmdbenchmark.update import DANGEROUS_COMPONENTS, runner


class _Logger:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.infos = []

    def log_error(self, msg, *a, **k):
        self.errors.append(str(msg))

    def log_warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    def log_info(self, msg, *a, **k):
        self.infos.append(str(msg))


def _args(**kw):
    base = {
        "user_set_overrides_by_stack": {},
        "setup_overrides_by_stack": {},
        "changed_flags": [],
        "component": None,
        "force": False,
        "step": None,
        "stack": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _user(overrides):
    return {"*": overrides}


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_nothing_to_change_is_refused():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(_args(), logger)
    assert any("needs something to change" in e for e in logger.errors)


def test_unknown_key_is_refused():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(
            _args(user_set_overrides_by_stack=_user({"noSuchKey": 1})), logger
        )
    assert any("noSuchKey" in e for e in logger.errors)


def test_dangerous_key_needs_force():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(
            _args(user_set_overrides_by_stack=_user({"model": {"name": "x"}})), logger
        )
    assert any("model" in e for e in logger.errors)


def test_dangerous_key_passes_with_force():
    logger = _Logger()
    components = runner.resolve_scope(
        _args(
            user_set_overrides_by_stack=_user({"model": {"name": "x"}}),
            force=True,
        ),
        logger,
    )
    assert "vllm" in components
    assert any("--force" in w for w in logger.warnings)


def test_safe_key_needs_no_force():
    logger = _Logger()
    components = runner.resolve_scope(
        _args(user_set_overrides_by_stack=_user({"decode": {"replicas": 4}})), logger
    )
    assert components == {"vllm", "fma"}
    assert logger.errors == []


@pytest.mark.parametrize("key", ["serviceAccount", "huggingface", "gateway", "lws"])
def test_warn_only_key_reaching_a_dangerous_component_needs_force(key):
    """The gate is on where a change lands, so these cannot walk past it."""
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(
            _args(user_set_overrides_by_stack=_user({key: {"x": 1}})), logger
        )
    errors = " ".join(logger.errors)
    assert f"via {key}" in errors


def test_warn_only_key_passes_with_force_and_still_warns():
    logger = _Logger()
    components = runner.resolve_scope(
        _args(
            user_set_overrides_by_stack=_user({"serviceAccount": {"name": "x"}}),
            force=True,
        ),
        logger,
    )
    assert "namespace" in components
    assert any("shared infrastructure" in w for w in logger.warnings)


def test_stack_scoped_set_outside_the_stack_filter_is_refused():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(
            _args(
                user_set_overrides_by_stack={"b": {"decode": {"replicas": 2}}},
                stack="a",
            ),
            logger,
        )
    assert any("leaves out" in e for e in logger.errors)


def test_stack_scoped_set_inside_the_stack_filter_passes():
    logger = _Logger()
    components = runner.resolve_scope(
        _args(
            user_set_overrides_by_stack={"a": {"decode": {"replicas": 2}}},
            stack="a,b",
        ),
        logger,
    )
    assert components == {"vllm", "fma"}


# ---------------------------------------------------------------------------
# Flags answer to the same scope and gate as the key they set
# ---------------------------------------------------------------------------


def test_a_changed_model_flag_needs_force():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(_args(changed_flags=["models"]), logger)
    assert any("--models" in e for e in logger.errors)


def test_a_changed_methods_flag_needs_force():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(_args(changed_flags=["methods"]), logger)
    assert any("--methods" in e for e in logger.errors)


def test_a_changed_flag_alone_is_something_to_change():
    logger = _Logger()
    components = runner.resolve_scope(_args(changed_flags=["affinity"]), logger)
    assert components == {"vllm", "standalone"}


def test_a_changed_monitoring_flag_is_scoped_like_the_key():
    logger = _Logger()
    components = runner.resolve_scope(_args(changed_flags=["monitoring"]), logger)
    assert "monitoring" in components
    assert any("--monitoring" in w for w in logger.warnings)


def test_a_noop_flag_says_so():
    logger = _Logger()
    components = runner.resolve_scope(_args(changed_flags=["full_infra"]), logger)
    assert components == set()
    assert any("--full-infra does not affect" in w for w in logger.warnings)


# ---------------------------------------------------------------------------
# --component answers to the same gate as --set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("component", sorted(DANGEROUS_COMPONENTS))
def test_dangerous_component_needs_force(component):
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(_args(component=component), logger)
    assert any(component in e for e in logger.errors)


def test_dangerous_component_passes_with_force():
    logger = _Logger()
    components = runner.resolve_scope(_args(component="namespace", force=True), logger)
    assert components == {"namespace"}


def test_safe_component_needs_no_force():
    logger = _Logger()
    assert runner.resolve_scope(_args(component="vllm"), logger) == {"vllm"}
    assert logger.errors == []


def test_unknown_component_is_refused():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(_args(component="nope"), logger)
    assert any("nope" in e for e in logger.errors)


def test_dangerous_set_and_dangerous_component_together_need_force():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(
            _args(
                user_set_overrides_by_stack=_user({"storage": {"x": 1}}),
                component="admin",
            ),
            logger,
        )
    errors = " ".join(logger.errors)
    assert "storage" in errors
    assert "admin" in errors, "both halves must be named, not just the first"


def test_dangerous_set_and_component_pass_together_with_force():
    logger = _Logger()
    components = runner.resolve_scope(
        _args(
            user_set_overrides_by_stack=_user({"storage": {"x": 1}}),
            component="admin",
            force=True,
        ),
        logger,
    )
    assert "admin" in components
    warned = " ".join(logger.warnings)
    assert "storage" in warned and "admin" in warned


# ---------------------------------------------------------------------------
# --cluster-config must not decide the scope
# ---------------------------------------------------------------------------


def test_cluster_config_keys_do_not_refuse_the_run():
    """storage in a --cluster-config file is not a change the user asked for."""
    logger = _Logger()
    components = runner.resolve_scope(
        _args(
            user_set_overrides_by_stack=_user({"decode": {"replicas": 4}}),
            setup_overrides_by_stack=_user(
                {"decode": {"replicas": 4}, "storage": {"storageClassName": "fast"}}
            ),
        ),
        logger,
    )
    assert components == {"vllm", "fma"}
    assert logger.errors == []


def test_cluster_config_alone_is_not_something_to_change():
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.resolve_scope(
            _args(setup_overrides_by_stack=_user({"storage": {"x": 1}})), logger
        )


# ---------------------------------------------------------------------------
# Step spec
# ---------------------------------------------------------------------------


def _ctx(methods=("modelservice",)):
    return argparse.Namespace(deployed_methods=list(methods))


def test_step_spec_comes_from_the_components():
    logger = _Logger()
    spec = runner.step_spec(_args(), _ctx(), {"epp"}, logger)
    assert spec == "6,7"


def test_step_override_wins_over_inference():
    logger = _Logger()
    spec = runner.step_spec(_args(step="7"), _ctx(), {"epp"}, logger)
    assert spec == "7"
    assert any("overrides" in i for i in logger.infos)


def test_step_override_alone_is_something_to_change():
    logger = _Logger()
    assert runner.resolve_scope(_args(step="8"), logger) == set()


@pytest.mark.parametrize("steps", ["2", "4", "2-4", "6,4"])
def test_step_override_reaching_a_dangerous_step_needs_force(steps):
    logger = _Logger()
    with pytest.raises(SystemExit):
        runner.step_spec(_args(step=steps), _ctx(), set(), logger)
    assert any("cannot be applied" in e for e in logger.errors)


def test_step_override_reaching_a_dangerous_step_passes_with_force():
    logger = _Logger()
    assert runner.step_spec(_args(step="4", force=True), _ctx(), set(), logger) == "4"


def test_step_five_is_dangerous_only_for_kustomize():
    logger = _Logger()
    assert (
        runner.step_spec(_args(step="5"), _ctx(("standalone",)), set(), logger) == "5"
    )
    with pytest.raises(SystemExit):
        runner.step_spec(_args(step="5"), _ctx(("kustomize",)), set(), logger)


def test_components_off_the_deployed_method_give_no_steps():
    logger = _Logger()
    assert runner.step_spec(_args(), _ctx(), {"nok8s"}, logger) == ""


def test_scope_summary_names_the_steps_given_with_s():
    ctx = _ctx()
    assert "component" not in runner.scope_summary(_args(step="8"), ctx, set(), "8")
    summary = runner.scope_summary(_args(), ctx, {"vllm", "standalone"}, "6,8")
    assert "vllm" in summary and "standalone" not in summary
