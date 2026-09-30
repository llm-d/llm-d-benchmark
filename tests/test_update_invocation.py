"""Tests for persisting and reusing the standup invocation.

Validates that:
- render-affecting flags are flattened for the Secret
- tri-state flags keep the difference between False and unset
- --set values survive a round trip, commas and newlines included
- a missing Secret and a failed read are told apart
- the flags go to a Secret, never to the ConfigMap
- reuse fills in what this invocation omits, and finds what it changes
- flags stored for some stacks only are not reused for the others
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from llmdbenchmark.utilities.standup_parameters import (
    CONFIGMAP_NAME,
    INVOCATION_FIELDS,
    READ_TIMEOUT_SECONDS,
    SECRET_NAME,
    TRISTATE_FIELDS,
    invocation_params,
    merge_invocation,
    read,
    reuse_invocation,
    stored_set_values,
    write,
)


def _args(**kwargs):
    defaults = {
        "set_overrides": None,
        "specification_file": None,
        "models": None,
        "methods": None,
        "gateway_class": None,
        "affinity": None,
        "annotations": None,
        "release": None,
        "monitoring": None,
        "prism": None,
        "wva": None,
        "epp_keda_saturation": None,
        "no_pvc": None,
        "full_infra": None,
        "cluster_config": None,
        "stack": None,
        "namespace": "ns",
        "reuse_invocation": True,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _result(stdout="", stderr="", success=True):
    return argparse.Namespace(stdout=stdout, stderr=stderr, success=success)


class _FakeCmd:
    """CommandExecutor stand-in that answers every `get` the same way."""

    _kube_bin = "kubectl"

    def __init__(self, stdout="", success=True, stderr=""):
        self._get = _result(stdout, stderr, success)
        self.calls = []
        self.kwargs = []
        self.written = {}

    def kube(self, *args, **kwargs):
        self.calls.append(args)
        self.kwargs.append(kwargs)
        if args[0] == "apply":
            manifest = yaml.safe_load(Path(args[2]).read_text())
            data = manifest["data"]
            if manifest["kind"] == "Secret":
                data = {k: base64.b64decode(v).decode() for k, v in data.items()}
            self.written[manifest["kind"]] = data
            return _result()
        return self._get


def _encode(data):
    return {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}


def _stored(**data):
    return _FakeCmd(json.dumps({"data": _encode(data)}))


def _stored_set(*values, **data):
    return _stored(invocation_set=json.dumps(list(values)), **data)


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def test_unset_flags_are_omitted():
    assert invocation_params(_args()) == {}


def test_set_values_are_stored_as_a_json_list():
    params = invocation_params(_args(set_overrides=["decode.replicas=2", "a=[1,2]"]))
    assert json.loads(params["invocation_set"]) == ["decode.replicas=2", "a=[1,2]"]


def test_a_multiline_set_value_survives_the_round_trip():
    """A --set may carry a whole YAML body; splitting on lines would cut it."""
    body = "router.epp.cfg=kind: X\nplugins:\n- type: a"
    params = invocation_params(_args(set_overrides=[body]))
    assert stored_set_values(params) == [body]


def test_on_flag_becomes_true():
    assert invocation_params(_args(wva=True))["invocation_wva"] == "true"


def test_off_flag_is_omitted():
    assert "invocation_wva" not in invocation_params(_args(wva=False))


def test_scalar_flags_are_stringified():
    params = invocation_params(_args(models="meta/x", gateway_class="istio"))
    assert params["invocation_models"] == "meta/x"
    assert params["invocation_gateway_class"] == "istio"


def test_cluster_config_path_is_stored_absolute(tmp_path, monkeypatch):
    """A relative path would point somewhere else from another directory."""
    monkeypatch.chdir(tmp_path)
    params = invocation_params(_args(cluster_config="cc.yaml"))
    assert params["invocation_cluster_config"] == str(tmp_path / "cc.yaml")


def test_stack_filter_is_stored():
    assert invocation_params(_args(stack="b,a"))["invocation_stack"] == "a,b"


def test_tristate_unset_is_omitted():
    assert "invocation_monitoring" not in invocation_params(_args(monitoring=None))


def test_tristate_false_is_stored():
    """--no-monitoring must not be confused with omitting --monitoring."""
    assert (
        invocation_params(_args(monitoring=False))["invocation_monitoring"] == "false"
    )


def test_tristate_fields_are_not_also_plain_fields():
    assert not set(TRISTATE_FIELDS) & set(INVOCATION_FIELDS)


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------


def test_read_returns_data():
    assert read(_stored(invocation_models="m"), "ns") == {"invocation_models": "m"}


def test_read_missing_secret_is_empty():
    cmd = _FakeCmd(success=False, stderr='Error from server (NotFound): "x" not found')
    assert read(cmd, "ns") == {}


def test_read_failure_is_none():
    """A timeout or auth error is not "no Secret": reuse must stop."""
    assert read(_FakeCmd(success=False, stderr="i/o timeout"), "ns") is None


def test_read_malformed_json_is_none():
    assert read(_FakeCmd("not json"), "ns") is None


def test_read_malformed_base64_is_none():
    assert read(_FakeCmd(json.dumps({"data": {"k": "%%%"}})), "ns") is None


def test_read_secret_without_data_is_empty():
    assert read(_FakeCmd(json.dumps({"metadata": {}})), "ns") == {}


def test_read_targets_the_named_secret():
    cmd = _stored()
    read(cmd, "my-ns")
    assert cmd.calls[0][:3] == ("get", "secret", SECRET_NAME)
    assert "my-ns" in cmd.calls[0]


def test_read_is_time_bounded_and_runs_under_dry_run():
    """kubectl blocks in the TCP dial against a stale tunnel, before
    --request-timeout applies; and the dry-run preview needs the stored flags."""
    cmd = _stored()
    read(cmd, "my-ns")
    assert cmd.kwargs[0]["timeout"] == READ_TIMEOUT_SECONDS
    assert cmd.kwargs[0]["force"] is True


# ---------------------------------------------------------------------------
# write / merge_invocation
# ---------------------------------------------------------------------------


def _ctx(tmp_path, invocation=None):
    import types

    return types.SimpleNamespace(
        harness_namespace="ns",
        require_namespace=lambda: "ns",
        setup_yamls_dir=lambda: tmp_path,
        invocation_params=invocation or {},
        dry_run=False,
        logger=MagicMock(),
    )


def test_write_keeps_the_flags_out_of_the_configmap(tmp_path):
    """A --set value may carry a token, and more users may read a ConfigMap."""
    cmd = _FakeCmd()
    ctx = _ctx(tmp_path, {"invocation_set": '["huggingface.token=hf_x"]'})
    assert write(cmd, ctx, {"model_name": "x"}) is True
    assert cmd.written["ConfigMap"] == {"model_name": "x"}
    assert cmd.written["Secret"] == {"invocation_set": '["huggingface.token=hf_x"]'}


def test_write_names_both_objects(tmp_path):
    cmd = _FakeCmd()
    write(cmd, _ctx(tmp_path), {})
    names = {
        yaml.safe_load(Path(call[2]).read_text())["metadata"]["name"]
        for call in cmd.calls
    }
    assert names == {CONFIGMAP_NAME, SECRET_NAME}


def test_write_keeps_a_multiline_value(tmp_path):
    """Built in Python: through a shell, a newline cuts the command."""
    cmd = _FakeCmd()
    write(cmd, _ctx(tmp_path, {"invocation_set": "a\nb"}), {})
    assert cmd.written["Secret"]["invocation_set"] == "a\nb"
    assert all(call[0] != "create" for call in cmd.calls)


def test_write_failure_warns(tmp_path):
    cmd = _FakeCmd()
    cmd.kube = lambda *a, **k: _result(success=False)
    ctx = _ctx(tmp_path)
    assert write(cmd, ctx, {}) is False
    assert ctx.logger.log_warning.called


def test_merge_replaces_the_flags_and_leaves_the_configmap(tmp_path):
    cmd = _stored(invocation_models="old", invocation_wva="true")
    assert merge_invocation(cmd, _ctx(tmp_path), _args(models="new")) is True
    assert cmd.written == {"Secret": {"invocation_models": "new"}}


# ---------------------------------------------------------------------------
# reuse_invocation: what is filled in
# ---------------------------------------------------------------------------


def _reuse(cmd, **kwargs):
    args = _args(**kwargs)
    logger = MagicMock()
    ok = reuse_invocation(args, cmd, logger)
    args.logger = logger
    args.ok = ok
    return args


def test_stored_set_pairs_go_before_the_typed_ones():
    args = _reuse(_stored_set("a.b=1", "c.d=2"), set_overrides=["a.b=9"])
    assert args.set_overrides == ["c.d=2", "a.b=9"]


def test_a_reused_multiline_value_is_kept_whole():
    body = "router.epp.cfg=kind: X\nplugins:\n- type: a"
    args = _reuse(_stored_set(body), set_overrides=["decode.replicas=2"])
    assert args.set_overrides == [body, "decode.replicas=2"]


def test_stack_scoped_stored_pair_survives_a_typed_global_pair():
    """Dropping it would revert that stack to the scenario default."""
    args = _reuse(
        _stored_set("llama:decode.replicas=4"), set_overrides=["decode.replicas=8"]
    )
    assert args.set_overrides == ["llama:decode.replicas=4", "decode.replicas=8"]


def test_same_selector_pair_is_not_reused_twice():
    args = _reuse(
        _stored_set("llama:decode.replicas=4"),
        set_overrides=["llama:decode.replicas=8"],
    )
    assert args.set_overrides == ["llama:decode.replicas=8"]


def test_a_stale_pair_packed_behind_another_is_dropped():
    args = _reuse(_stored_set("b=1,c=1"), set_overrides=["c=2"])
    assert args.set_overrides == ["b=1", "c=2"]


def test_unparseable_stored_pair_warns_instead_of_vanishing():
    args = _reuse(_stored_set("decode.replicas=1", "BROKEN", "vllm.foo=2"))
    assert args.set_overrides == ["decode.replicas=1", "vllm.foo=2"]
    warned = " ".join(str(c) for c in args.logger.log_warning.call_args_list)
    assert "BROKEN" in warned


def test_string_flag_valued_true_stays_a_string():
    args = _reuse(_stored(invocation_release="true", invocation_wva="true"))
    assert args.release == "true"
    assert args.wva is True


def test_a_typed_flag_wins():
    args = _reuse(_stored(invocation_models="old"), models="new")
    assert args.models == "new"


def test_an_on_flag_can_be_turned_off():
    """--no-wva is given, so the stored true must not come back."""
    args = _reuse(_stored(invocation_wva="true"), wva=False)
    assert args.wva is False
    assert args.changed_flags == ["wva"]


def test_tristate_flags_are_reused_both_ways():
    args = _reuse(_stored(invocation_monitoring="false", invocation_prism="true"))
    assert args.monitoring is False
    assert args.prism is True


def test_reuse_reads_the_harness_namespace():
    cmd = _stored()
    _reuse(cmd, namespace="infra-ns,harness-ns")
    assert "harness-ns" in cmd.calls[0]


def test_a_reused_cluster_config_is_set_when_readable(tmp_path):
    cc = tmp_path / "cluster.yaml"
    cc.write_text("storage: {}\n")
    args = _reuse(_stored(invocation_cluster_config=str(cc)))
    assert args.cluster_config == str(cc)


def test_an_unreadable_reused_cluster_config_warns(tmp_path):
    args = _reuse(_stored(invocation_cluster_config=str(tmp_path / "gone.yaml")))
    assert args.cluster_config is None
    assert args.logger.log_warning.called


def test_a_different_spec_warns():
    args = _reuse(_stored(invocation_spec="a.yaml.j2"), specification_file="b.yaml.j2")
    assert args.ok
    assert "stood up" in str(args.logger.log_warning.call_args)


# ---------------------------------------------------------------------------
# reuse_invocation: when it stops, and what it skips
# ---------------------------------------------------------------------------


def test_reuse_needs_a_namespace():
    """Without -p the read would find nothing and the flags be reverted."""
    args = _reuse(_stored(invocation_models="m"), namespace=None)
    assert args.ok is False


def test_no_namespace_is_fine_without_reuse():
    args = _reuse(_stored(), namespace=None, reuse_invocation=False)
    assert args.ok is True


def test_a_failed_read_stops_the_update():
    args = _reuse(_FakeCmd(success=False, stderr="i/o timeout"))
    assert args.ok is False


def test_a_missing_secret_warns_and_goes_on():
    args = _reuse(_FakeCmd(success=False, stderr="(NotFound)"), models="m")
    assert args.ok is True
    assert args.logger.log_warning.called
    assert args.changed_flags == ["models"]


def test_no_reuse_still_compares_with_the_stored_flags():
    """Typing the standup's flags again is not a change."""
    args = _reuse(_stored(invocation_models="m"), models="m", reuse_invocation=False)
    assert args.changed_flags == []
    assert args.methods is None, "nothing may be filled in"


def test_flags_stored_for_some_stacks_are_not_reused_for_all():
    args = _reuse(_stored(invocation_models="x", invocation_stack="a"))
    assert args.models is None
    assert args.record_invocation is False


def test_flags_stored_for_a_stack_are_reused_for_that_stack():
    args = _reuse(_stored(invocation_models="x", invocation_stack="a"), stack="a")
    assert args.models == "x"
    assert args.record_invocation is True


def test_an_update_of_fewer_stacks_is_not_recorded():
    """One set of flags cannot hold a change for some stacks only."""
    args = _reuse(_stored(invocation_models="x"), stack="a")
    assert args.models == "x"
    assert args.record_invocation is False
    assert args.stored_invocation == {"invocation_models": "x"}


# ---------------------------------------------------------------------------
# reuse_invocation: what counts as a change
# ---------------------------------------------------------------------------


def test_a_typed_pair_equal_to_the_stored_one_is_not_a_change():
    args = _reuse(
        _stored_set("model.name=X"), set_overrides=["model.name=X", "decode.replicas=2"]
    )
    assert args.changed_set_overrides == ["decode.replicas=2"]


def test_a_typed_pair_with_a_new_value_is_a_change():
    args = _reuse(_stored_set("decode.replicas=1"), set_overrides=["decode.replicas=2"])
    assert args.changed_set_overrides == ["decode.replicas=2"]


def test_a_typed_flag_equal_to_the_stored_one_is_not_a_change():
    args = _reuse(
        _stored(invocation_models="m", invocation_wva="true"), models="m", wva=True
    )
    assert args.changed_flags == []


def test_a_typed_flag_with_a_new_value_is_a_change():
    args = _reuse(_stored(invocation_models="m"), models="other")
    assert args.changed_flags == ["models"]


def test_reused_pairs_do_not_scope_the_update():
    """A standup that set model.name must not make every later update need --force."""
    from llmdbenchmark import cli
    from llmdbenchmark.update import classify_overrides

    args = _reuse(_stored_set("model.name=X"), set_overrides=["router.epp.replicas=2"])
    args.command = "update"
    args.run_description = None
    args.run_keywords = None
    args.cluster_config_overrides = None

    buckets = cli._build_setup_overrides_by_stack(args, MagicMock())

    assert buckets["*"]["model"]["name"] == "X", "render still gets the reused value"
    _, _, dangerous, _, _ = classify_overrides(args.user_set_overrides_by_stack)
    assert dangerous == []


def test_synthetic_overrides_do_not_scope_the_update():
    """--no-pvc and --cluster-config land in the buckets, not in the scope."""
    from llmdbenchmark import cli

    args = _reuse(_stored(), set_overrides=["decode.replicas=2"], no_pvc=True)
    args.command = "update"
    args.run_description = "x"
    args.run_keywords = None
    args.cluster_config_overrides = {"storage": {"storageClassName": "fast"}}

    buckets = cli._build_setup_overrides_by_stack(args, MagicMock())

    assert {"storage", "modelservice", "description"} <= set(buckets["*"])
    assert args.user_set_overrides_by_stack == {"*": {"decode": {"replicas": 2}}}


# ---------------------------------------------------------------------------
# The whole preparation, in the order the CLI runs it
# ---------------------------------------------------------------------------


def _prepare(monkeypatch, cmd, **kwargs):
    from llmdbenchmark import cli

    monkeypatch.setattr(cli, "_reuse_cmd", lambda *a: cmd)
    args = _args(**kwargs)
    args.command = "update"
    args.run_description = None
    args.run_keywords = None
    cli._prepare_overrides(args, MagicMock())
    return args


def test_a_reused_cluster_config_is_loaded(monkeypatch, tmp_path):
    """Reuse may supply the path, so the file must be read after it."""
    cc = tmp_path / "cluster.yaml"
    cc.write_text("storage:\n  storageClassName: from-reused-file\n")
    args = _prepare(monkeypatch, _stored(invocation_cluster_config=str(cc)))
    assert (
        args.setup_overrides_by_stack["*"]["storage"]["storageClassName"]
        == "from-reused-file"
    )


def test_set_from_the_env_is_a_change_and_is_stored(monkeypatch):
    monkeypatch.setenv("LLMDBENCH_SET", "decode.replicas=4")
    args = _prepare(monkeypatch, _stored_set("router.epp.replicas=2"))
    assert args.user_set_overrides_by_stack == {"*": {"decode": {"replicas": 4}}}
    assert args.set_overrides == ["router.epp.replicas=2", "decode.replicas=4"]
    assert stored_set_values(invocation_params(args)) == args.set_overrides
