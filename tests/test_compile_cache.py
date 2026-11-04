"""Tests for ``vllmCommon.cacheMountPath``.

Puts the vLLM/flashinfer/triton compile caches on the workload PVC so a
restarted pod skips most of the warmup. Env, mount and volume must always
come together, and the cache must stay off where the PVC cannot hold it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.executor.command import CommandResult
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.standup.steps.step_05_standalone_deploy import StandaloneDeployStep

_CACHE_ENV = ("FLASHINFER_WORKSPACE_BASE", "TRITON_CACHE_DIR")


class _Logger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, *_: Any, **__: Any) -> None:
        pass

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass

    def line_break(self) -> None:
        pass


def _render(
    tmp_path: Path,
    overrides: dict | None = None,
    scenario: str = "guides/wide-ep",
) -> Path:
    root = Path(__file__).resolve().parents[1]
    result = RenderPlans(
        template_dir=root / "config" / "templates" / "jinja",
        defaults_file=root / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=root / "config" / "scenarios" / f"{scenario}.yaml",
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        setup_overrides=overrides,
    ).eval()

    assert not result.has_errors
    return next((tmp_path / "plan").iterdir())


def _load(plan: Path, name: str) -> Any:
    return yaml.safe_load((plan / name).read_text(encoding="utf-8"))


def _env(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e["value"] for e in container.get("env") or [] if "value" in e}


def _ms_roles(plan: Path) -> list[dict[str, Any]]:
    values = _load(plan, "13_ms-values.yaml")
    return [values[role] for role in ("decode", "prefill")]


def _standalone_spec(plan: Path) -> dict[str, Any]:
    docs = yaml.safe_load_all(
        (plan / "14_standalone-deployment_yaml.yaml").read_text(encoding="utf-8")
    )
    deployment = next(d for d in docs if d and d["kind"] == "Deployment")
    return deployment["spec"]["template"]["spec"]


def _assert_cache_off(roles: list[dict[str, Any]]) -> None:
    for role in roles:
        env = _env(role["containers"][0])
        assert env["VLLM_CACHE_ROOT"] == "/tmp/vllm"
        assert not set(_CACHE_ENV) & set(env)
        assert "compile-cache" not in {v["name"] for v in role.get("volumes") or []}


def test_modelservice_cache_on_workload_pvc(tmp_path: Path) -> None:
    for role in _ms_roles(_render(tmp_path)):
        container = role["containers"][0]
        env = _env(container)
        assert env["VLLM_CACHE_ROOT"] == "/compile-cache/.vllm-cache/vllm"
        assert env["FLASHINFER_WORKSPACE_BASE"] == "/compile-cache/.vllm-cache"
        assert env["TRITON_CACHE_DIR"] == "/compile-cache/.vllm-cache/triton"

        assert {
            "name": "compile-cache",
            "persistentVolumeClaim": {"claimName": "workload-pvc"},
        } in role["volumes"]
        mounts = {m["name"]: m for m in container["volumeMounts"]}
        assert mounts["compile-cache"] == {
            "name": "compile-cache",
            "mountPath": "/compile-cache",
        }
        claims = [
            v["persistentVolumeClaim"]["claimName"]
            for v in role["volumes"]
            if "persistentVolumeClaim" in v
        ]
        assert claims.count("workload-pvc") == 1

        init_containers = role["initContainers"]
        assert init_containers
        for ic in init_containers:
            ic_env = _env(ic)
            assert ic_env["VLLM_CACHE_ROOT"] == "/tmp/vllm"
            assert not set(_CACHE_ENV) & set(ic_env)


def test_custom_mount_path(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"vllmCommon": {"cacheMountPath": "/cc"}})
    for role in _ms_roles(plan):
        container = role["containers"][0]
        assert _env(container)["TRITON_CACHE_DIR"] == "/cc/.vllm-cache/triton"
        mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
        assert mounts["compile-cache"] == "/cc"


def test_explicit_cache_root_wins(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"decode": {"vllm": {"cacheRoot": "/data/vc"}}})
    decode, prefill = _ms_roles(plan)
    assert _env(decode["containers"][0])["VLLM_CACHE_ROOT"] == "/data/vc"
    assert _env(decode["containers"][0])["FLASHINFER_WORKSPACE_BASE"] == (
        "/compile-cache/.vllm-cache"
    )
    assert _env(prefill["containers"][0])["VLLM_CACHE_ROOT"] == (
        "/compile-cache/.vllm-cache/vllm"
    )


def test_disabled_keeps_cache_in_container(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"vllmCommon": {"cacheMountPath": ""}})
    _assert_cache_off(_ms_roles(plan))


def test_not_read_write_many_turns_cache_off(tmp_path: Path) -> None:
    overrides = {"storage": {"workloadPvc": {"accessModes": ["ReadWriteOnce"]}}}
    plan = _render(tmp_path, overrides)
    assert _load(plan, "config.yaml")["vllmCommon"]["cacheMountPath"] == ""
    _assert_cache_off(_ms_roles(plan))


def test_hf_models_still_get_the_cache(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"modelservice": {"uriProtocol": "hf"}})
    for role in _ms_roles(plan):
        assert _env(role["containers"][0])["FLASHINFER_WORKSPACE_BASE"] == (
            "/compile-cache/.vllm-cache"
        )


def test_fma_keeps_its_own_cache(tmp_path: Path) -> None:
    plan = _render(tmp_path, scenario="examples/fma")
    assert _load(plan, "config.yaml")["vllmCommon"]["cacheMountPath"] == ""
    fma = (plan / "24_fma-deployment.yaml").read_text(encoding="utf-8")
    assert "compile-cache" not in fma


def test_nok8s_turns_cache_off(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"nok8s": {"enabled": True}})
    assert _load(plan, "config.yaml")["vllmCommon"]["cacheMountPath"] == ""


def test_kustomize_turns_cache_off(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"kustomize": {"enabled": True}})
    assert _load(plan, "config.yaml")["vllmCommon"]["cacheMountPath"] == ""


def test_no_pvc_render_keeps_cache_in_container(tmp_path: Path) -> None:
    import argparse

    from llmdbenchmark.cli import _no_pvc_standup_overrides

    overrides = _no_pvc_standup_overrides(
        argparse.Namespace(command="standup", no_pvc=True)
    )
    _assert_cache_off(_ms_roles(_render(tmp_path, overrides)))


def test_trailing_slash_in_mount_path(tmp_path: Path) -> None:
    plan = _render(tmp_path, {"vllmCommon": {"cacheMountPath": "/cc/"}})
    for role in _ms_roles(plan):
        env = _env(role["containers"][0])
        assert env["TRITON_CACHE_DIR"] == "/cc/.vllm-cache/triton"


def _names(container: dict[str, Any]) -> list[str]:
    return [e["name"] for e in container.get("env") or []]


def test_user_env_wins_without_duplicate(tmp_path: Path) -> None:
    extra = [{"name": "TRITON_CACHE_DIR", "value": "/mine"}]
    plan = _render(tmp_path, {"decode": {"extraEnvVars": extra}})
    decode, prefill = _ms_roles(plan)
    assert _names(decode["containers"][0]).count("TRITON_CACHE_DIR") == 1
    assert _env(decode["containers"][0])["TRITON_CACHE_DIR"] == "/mine"
    assert _env(prefill["containers"][0])["TRITON_CACHE_DIR"] == (
        "/compile-cache/.vllm-cache/triton"
    )


def test_user_cache_root_has_no_duplicate_in_init_containers(tmp_path: Path) -> None:
    extra = [{"name": "VLLM_CACHE_ROOT", "value": "/x"}]
    plan = _render(tmp_path, {"decode": {"extraEnvVars": extra}})
    decode = _ms_roles(plan)[0]
    assert decode["initContainers"]
    for container in decode["containers"] + decode["initContainers"]:
        assert _names(container).count("VLLM_CACHE_ROOT") == 1
        assert _env(container)["VLLM_CACHE_ROOT"] == "/x"


def test_standalone_user_env_and_cache_root(tmp_path: Path) -> None:
    overrides = {
        "standalone": {
            "enabled": True,
            "vllm": {"cacheRoot": "/data/sc"},
            "extraEnvVars": [{"name": "TRITON_CACHE_DIR", "value": "/mine"}],
        }
    }
    spec = _standalone_spec(_render(tmp_path, overrides, "guides/pd-disaggregation"))
    container = spec["containers"][0]
    assert _names(container).count("TRITON_CACHE_DIR") == 1
    env = _env(container)
    assert env["TRITON_CACHE_DIR"] == "/mine"
    assert env["VLLM_CACHE_ROOT"] == "/data/sc"
    assert env["FLASHINFER_WORKSPACE_BASE"] == "/compile-cache/.vllm-cache"


def _no_blank_lines(path: Path) -> None:
    # The env line after the cache env must follow it directly
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines[1:], 1):
        if "name: VLLM_WORKER_MULTIPROC_METHOD" in line:
            assert lines[i - 1].strip(), f"blank line before {line!r} in {path.name}"


def test_cache_env_leaves_no_blank_line(tmp_path: Path) -> None:
    all_user = [
        {"name": n, "value": "/u"}
        for n in ("VLLM_CACHE_ROOT", "FLASHINFER_WORKSPACE_BASE", "TRITON_CACHE_DIR")
    ]
    cases = {
        "on": {},
        "off": {"vllmCommon": {"cacheMountPath": ""}},
        "all-user": {
            "decode": {"extraEnvVars": all_user},
            "prefill": {"extraEnvVars": all_user},
            "standalone": {"enabled": True, "extraEnvVars": all_user},
        },
    }
    for name, overrides in cases.items():
        plan = _render(tmp_path / name, overrides, "guides/pd-disaggregation")
        _no_blank_lines(plan / "13_ms-values.yaml")
        _no_blank_lines(plan / "14_standalone-deployment_yaml.yaml")


def test_relative_mount_path_is_rejected(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = RenderPlans(
        template_dir=root / "config" / "templates" / "jinja",
        defaults_file=root / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=root / "config" / "scenarios" / "guides" / "wide-ep.yaml",
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        setup_overrides={"vllmCommon": {"cacheMountPath": "cc"}},
    )
    try:
        outcome = result.eval()
    except ValueError as e:
        assert "absolute" in str(e)
        return
    assert outcome.has_errors


def test_direct_service_in_other_namespace_turns_cache_off(tmp_path: Path) -> None:
    overrides = {"gateway": {"className": "none", "namespace": "serving"}}
    plan = _render(tmp_path, overrides, "guides/optimized-baseline")
    assert _load(plan, "config.yaml")["vllmCommon"]["cacheMountPath"] == ""
    _assert_cache_off(_ms_roles(plan)[:1])


def test_direct_service_in_same_namespace_keeps_cache(tmp_path: Path) -> None:
    plan = _render(
        tmp_path, {"gateway": {"className": "none"}}, "guides/optimized-baseline"
    )
    config = _load(plan, "config.yaml")
    assert config["gateway"]["namespace"] == config["namespace"]["name"]
    assert config["vllmCommon"]["cacheMountPath"] == "/compile-cache"


def test_standalone_and_launcher_get_the_cache(tmp_path: Path) -> None:
    overrides = {"standalone": {"enabled": True, "launcher": {"enabled": True}}}
    spec = _standalone_spec(_render(tmp_path, overrides, "guides/pd-disaggregation"))

    assert len(spec["containers"]) == 2
    for container in spec["containers"]:
        env = _env(container)
        assert env["VLLM_CACHE_ROOT"] == "/compile-cache/.vllm-cache/vllm"
        assert env["FLASHINFER_WORKSPACE_BASE"] == "/compile-cache/.vllm-cache"
        mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
        assert mounts["compile-cache"] == "/compile-cache"

    assert {
        "name": "compile-cache",
        "persistentVolumeClaim": {"claimName": "workload-pvc"},
    } in spec["volumes"]


class _Cmd:
    def __init__(
        self,
        access_modes: str | None,
        modes_readable: bool = True,
        binds: bool = True,
        bind_skipped: bool = False,
        apply_stderr: str | None = None,
        size: str = "20Gi",
        phase: str = "Pending",
    ):
        # None means the PVC does not exist until it is applied
        self.access_modes = access_modes
        self.modes_readable = modes_readable
        self.binds = binds
        self.bind_skipped = bind_skipped
        self.apply_stderr = apply_stderr
        self.size = size
        self.phase = phase
        self.calls: list[tuple] = []
        self.pvc_waits = 0

    def kube(self, *args: str, **_: Any) -> CommandResult:
        self.calls.append(args)
        if args[0] == "apply":
            self.access_modes = '["ReadWriteMany"]'
            if self.apply_stderr is not None:
                return CommandResult(command="", exit_code=1, stderr=self.apply_stderr)
            return CommandResult(command="", exit_code=0)
        if args[:2] == ("get", "pvc"):
            if self.access_modes is None:
                return CommandResult(command="", exit_code=1)
            if "accessModes" in args[-1]:
                if not self.modes_readable:
                    return CommandResult(command="", exit_code=1, stderr="forbidden")
                return CommandResult(command="", exit_code=0, stdout=self.access_modes)
            if "phase" in args[-1]:
                return CommandResult(command="", exit_code=0, stdout=self.phase)
            return CommandResult(command="", exit_code=0, stdout=self.size)
        return CommandResult(command="", exit_code=0)

    def wait_for_pvc(self, **_: Any) -> CommandResult:
        self.pvc_waits += 1
        return CommandResult(
            command="",
            exit_code=0 if self.binds else 1,
            wait_skipped=self.bind_skipped,
        )


def _stack(
    tmp_path: Path,
    cache: str,
    with_pvc_yaml: bool = True,
    name: str = "stack01",
    pvc_size: str = "20Gi",
) -> Path:
    stack = tmp_path / "plan" / name
    stack.mkdir(parents=True)
    config = {
        "vllmCommon": {"cacheMountPath": cache},
        "storage": {"workloadPvc": {"name": "workload-pvc", "size": "20Gi"}},
        "harness": {"pvcSize": pvc_size},
    }
    (stack / "config.yaml").write_text(yaml.dump(config), encoding="utf-8")
    if with_pvc_yaml:
        (stack / "01_pvc_workload-pvc.yaml").write_text("kind: PVC\n", encoding="utf-8")
    return stack


def _ensure_with_hint(
    tmp_path: Path,
    cmd: _Cmd,
    cache: str = "/compile-cache",
    dry_run: bool = False,
    with_pvc_yaml: bool = True,
) -> tuple[list, str]:
    stack = _stack(tmp_path, cache, with_pvc_yaml)
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        logger=_Logger(),
        namespace="ns",
        rendered_stacks=[stack],
        dry_run=dry_run,
    )
    errors: list = []
    config = yaml.safe_load((stack / "config.yaml").read_text(encoding="utf-8"))
    hint = StandaloneDeployStep()._ensure_compile_cache_pvc(
        cmd, context, config, errors
    )
    return errors, hint


def _ensure(tmp_path: Path, cmd: _Cmd, **kwargs: Any) -> list:
    return _ensure_with_hint(tmp_path, cmd, **kwargs)[0]


def _applied(cmd: _Cmd) -> bool:
    return any(c[0] == "apply" for c in cmd.calls)


def test_creates_missing_workload_pvc(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes=None)
    assert _ensure(tmp_path, cmd) == []
    assert _applied(cmd)
    assert cmd.pvc_waits == 1


def test_skips_workload_pvc_when_cache_off(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes=None)
    assert _ensure(tmp_path, cmd, cache="") == []
    assert cmd.calls == []


def test_rejects_existing_read_write_once_pvc(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes='["ReadWriteOnce"]')
    errors = _ensure(tmp_path, cmd)
    assert len(errors) == 1 and "ReadWriteMany" in errors[0]
    assert not _applied(cmd)


def test_existing_pvc_must_still_bind(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes='["ReadWriteMany"]', binds=False)
    errors = _ensure(tmp_path, cmd)
    assert not _applied(cmd)
    assert cmd.pvc_waits == 1
    assert len(errors) == 1 and "did not bind" in errors[0]


def test_unreadable_access_modes_fail(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes='["ReadWriteMany"]', modes_readable=False)
    errors = _ensure(tmp_path, cmd)
    assert len(errors) == 1 and "forbidden" in errors[0]


def test_dry_run_still_shows_apply_and_wait(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes=None)
    assert _ensure(tmp_path, cmd, dry_run=True) == []
    assert _applied(cmd)
    assert cmd.pvc_waits == 1
    assert not any(c[:2] == ("get", "pvc") for c in cmd.calls)


def test_apply_failure_stops(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes=None, apply_stderr="quota exceeded")
    errors = _ensure(tmp_path, cmd)
    assert len(errors) == 1 and "quota exceeded" in errors[0]
    assert cmd.pvc_waits == 0


def test_apply_race_already_exists_is_fine(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes=None, apply_stderr="AlreadyExists")
    assert _ensure(tmp_path, cmd) == []
    assert cmd.pvc_waits == 1


def test_too_small_pvc_stops_before_wait(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes='["ReadWriteMany"]', size="1Gi")
    errors = _ensure(tmp_path, cmd)
    assert len(errors) == 1 and "too small" in errors[0]
    assert cmd.pvc_waits == 0


def test_missing_pvc_manifest_fails(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes=None)
    errors = _ensure(tmp_path, cmd, with_pvc_yaml=False)
    assert len(errors) == 1 and "01_pvc_workload-pvc" in errors[0]
    assert cmd.calls == []


def test_deferred_bind_gives_hint(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes=None, bind_skipped=True)
    errors, hint = _ensure_with_hint(tmp_path, cmd)
    assert errors == []
    assert "WaitForFirstConsumer" in hint


def test_bound_pvc_gives_no_hint(tmp_path: Path) -> None:
    errors, hint = _ensure_with_hint(tmp_path, _Cmd(access_modes=None))
    assert errors == [] and hint == ""


def test_bound_pvc_on_wait_for_first_consumer_gives_no_hint(tmp_path: Path) -> None:
    cmd = _Cmd(access_modes='["ReadWriteMany"]', bind_skipped=True, phase="Bound")
    errors, hint = _ensure_with_hint(tmp_path, cmd)
    assert errors == [] and hint == ""


def test_hint_names_the_namespace(tmp_path: Path) -> None:
    _, hint = _ensure_with_hint(tmp_path, _Cmd(access_modes=None, bind_skipped=True))
    assert "-n ns" in hint


def test_shared_pvc_is_sized_from_the_first_stack(tmp_path: Path) -> None:
    first = _stack(tmp_path, "/compile-cache", pvc_size="20Gi")
    second = _stack(
        tmp_path, "/compile-cache", with_pvc_yaml=False, name="stack02", pvc_size="99Gi"
    )
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        logger=_Logger(),
        namespace="ns",
        rendered_stacks=[first, second],
    )
    config = yaml.safe_load((second / "config.yaml").read_text(encoding="utf-8"))
    errors: list = []
    cmd = _Cmd(access_modes='["ReadWriteMany"]', size="20Gi")
    StandaloneDeployStep()._ensure_compile_cache_pvc(cmd, context, config, errors)
    assert errors == []


def test_no_pvc_warning_once_per_run(tmp_path: Path) -> None:
    class _CountLogger(_Logger):
        warnings = 0

        def log_warning(self, *_: Any, **__: Any) -> None:
            _CountLogger.warnings += 1

    stack = _stack(tmp_path, "/compile-cache")
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        logger=_CountLogger(),
        namespace="ns",
        rendered_stacks=[stack],
        no_pvc=True,
    )
    config = yaml.safe_load((stack / "config.yaml").read_text(encoding="utf-8"))
    for _ in range(2):
        StandaloneDeployStep()._ensure_compile_cache_pvc(
            _Cmd(access_modes=None), context, config, []
        )
    assert _CountLogger.warnings == 1


def test_with_hint_has_single_period() -> None:
    step = StandaloneDeployStep()
    assert step._with_hint("Timed out. budget used: 1/2.", "Check it.") == (
        "Timed out. budget used: 1/2. Check it."
    )
    assert step._with_hint("Timed out", "Check it.") == "Timed out. Check it."
    assert step._with_hint("Timed out.", "") == "Timed out."


class _StepCmd(_Cmd):
    _kube_bin = "kubectl"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.pod_waits = 0

    def wait_for_pods(self, **_: Any) -> CommandResult:
        self.pod_waits += 1
        return CommandResult(command="", exit_code=1, stderr="timed out")


def _standalone_stack(tmp_path: Path) -> tuple[Path, ExecutionContext]:
    stack = _stack(tmp_path, "/compile-cache")
    config = yaml.safe_load((stack / "config.yaml").read_text(encoding="utf-8"))
    config["standalone"] = {"replicas": 1}
    config["model"] = {"name": "m", "shortName": "m"}
    config["vllmCommon"]["inferencePort"] = 8000
    config["release"] = "r"
    (stack / "config.yaml").write_text(yaml.dump(config), encoding="utf-8")
    (stack / "14_standalone-deployment.yaml").write_text(
        "kind: Deployment\nmetadata:\n  name: vllm-standalone\n", encoding="utf-8"
    )
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        logger=_Logger(),
        namespace="ns",
        rendered_stacks=[stack],
        dry_run=False,
    )
    return stack, context


def _deployment_applied(cmd: _Cmd) -> bool:
    return any(
        c[0] == "apply" and "14_standalone-deployment" in c[-1] for c in cmd.calls
    )


def test_step_fails_without_deploying_when_pvc_not_ready(tmp_path: Path) -> None:
    stack, context = _standalone_stack(tmp_path)
    cmd = _StepCmd(access_modes='["ReadWriteOnce"]')
    context.cmd = cmd
    result = StandaloneDeployStep().execute(context, stack)
    assert not result.success
    assert result.message == "Compile cache PVC not ready"
    assert not _deployment_applied(cmd)


def test_step_adds_hint_when_pods_not_ready(tmp_path: Path) -> None:
    stack, context = _standalone_stack(tmp_path)
    cmd = _StepCmd(access_modes=None, bind_skipped=True)
    context.cmd = cmd
    result = StandaloneDeployStep().execute(context, stack)
    assert not result.success
    assert _deployment_applied(cmd)
    assert any("WaitForFirstConsumer" in e for e in result.errors)
    assert not any(".." in e for e in result.errors)
