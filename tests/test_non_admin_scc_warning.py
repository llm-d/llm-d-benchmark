"""Non-admin standup must say why a runAsUser:0 scenario will not schedule.

``--non-admin`` cannot run ``oc adm policy add-scc-to-user``, so a scenario
that asks for ``runAsUser: 0`` or added capabilities (e.g.
``guides/optimized-baseline``, which adds IPC_LOCK + SYS_RAWIO for NIXL)
deploys a Deployment whose pods OpenShift refuses to create:

    pods "...-decode-..." is forbidden: unable to validate against any
    security context constraint: [... provider restricted-v2:
    .containers[0].runAsUser: Invalid value: 0 ...]

The failure surfaces minutes later as ``ReplicaFailure/FailedCreate`` on a
ReplicaSet with no pod events, which says nothing about the skipped grant.
Standup has the plan config in hand and must name the problem up front.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml

from llmdbenchmark.parser.cli_overrides import parse_cli_overrides
from llmdbenchmark.parser.cluster_resource_resolver import ClusterResourceResolver
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.standup.steps.step_08_deploy_modelservice import (
    DeployModelserviceStep,
)

_REPO = Path(__file__).resolve().parents[1]
_SCENARIOS = _REPO / "config/scenarios/guides"

_NAMESPACE = "llmdbench"
_SA = "qwen-qwen3-32b"


def _context(non_admin: bool) -> SimpleNamespace:
    return SimpleNamespace(
        non_admin=non_admin,
        is_openshift=True,
        logger=MagicMock(),
    )


def _elevated_plan_config() -> dict:
    """The securityContext shape ``guides/optimized-baseline`` renders."""
    return {
        "model_id_label": _SA,
        "decode": {
            "extraContainerConfig": {
                "securityContext": {
                    "capabilities": {"add": ["IPC_LOCK", "SYS_RAWIO"]},
                    "runAsGroup": 0,
                    "runAsUser": 0,
                }
            }
        },
    }


def _warning_text(context: SimpleNamespace) -> str:
    return "\n".join(
        str(call.args[0]) for call in context.logger.log_warning.call_args_list
    )


def _scc_grant_calls(cmd: MagicMock) -> list:
    return [c for c in cmd.kube.call_args_list if "add-scc-to-user" in c.args]


def test_non_admin_warns_instead_of_granting_scc() -> None:
    """The RED case: non-admin must warn, and must not attempt the grant."""
    step = DeployModelserviceStep()
    cmd = MagicMock()
    context = _context(non_admin=True)

    step._manage_sccs(cmd, context, _elevated_plan_config(), _NAMESPACE)

    assert _scc_grant_calls(cmd) == [], (
        "--non-admin cannot grant SCCs; it must not shell out to "
        "`oc adm policy add-scc-to-user`"
    )

    warning = _warning_text(context)
    assert warning, "non-admin + runAsUser:0 must produce a warning"
    assert _SA in warning, "the warning must name the ServiceAccount to grant"
    assert _NAMESPACE in warning, "the warning must name the namespace"
    assert "add-scc-to-user" in warning, (
        "the warning must carry the remediation command"
    )
    assert "security context constraint" in warning, (
        "the warning must quote the admission error the user will otherwise hit"
    )


def test_non_admin_warning_offers_the_set_escape_hatch() -> None:
    """Both ways out, not just the one that needs somebody else.

    ``--set`` can drop the request instead: ``capabilities.add`` takes an
    empty list, and ``runAsUser`` can be re-pointed at a UID from the
    namespace's ``openshift.io/sa.scc.uid-range``. It cannot be *removed*
    (null is ignored by the config merge and '' renders an empty string
    where the API server wants an int64), which is why the warning has to
    name the annotation rather than just say "unset it".
    """
    step = DeployModelserviceStep()
    cmd = MagicMock()
    context = _context(non_admin=True)

    step._manage_sccs(cmd, context, _elevated_plan_config(), _NAMESPACE)

    warning = _warning_text(context)
    assert "add-scc-to-user" in warning, "must offer the cluster-admin route"
    assert "capabilities.add=[]" in warning, (
        "must offer the --set route for clearing the added capabilities"
    )
    assert "runAsUser" in warning, "must tell the user to re-point runAsUser"
    assert "uid-range" in warning, (
        "a UID is only valid inside the namespace's range, so the warning "
        "must say where to read it from"
    )


def test_non_admin_stays_quiet_when_no_elevation_is_requested() -> None:
    """A scenario that does not ask for root needs no SCC grant or warning."""
    step = DeployModelserviceStep()
    cmd = MagicMock()
    context = _context(non_admin=True)

    step._manage_sccs(cmd, context, {"model_id_label": _SA, "decode": {}}, _NAMESPACE)

    assert _scc_grant_calls(cmd) == []
    assert _warning_text(context) == ""


def test_admin_still_grants_anyuid_and_privileged() -> None:
    """Regression guard: the admin path keeps granting both SCCs."""
    step = DeployModelserviceStep()
    cmd = MagicMock()
    context = _context(non_admin=False)

    step._manage_sccs(cmd, context, _elevated_plan_config(), _NAMESPACE)

    granted = {c.args[3] for c in _scc_grant_calls(cmd)}
    assert granted == {"anyuid", "privileged"}


def test_the_printed_set_remediation_really_clears_the_request(tmp_path) -> None:
    """The recipe the warning prints has to actually work.

    Renders the base guide with exactly those two ``--set`` pairs and checks
    the values the modelservice chart receives. This guards the advice: the
    merge semantics it relies on are specific (a list replaces, a scalar
    replaces, ``null`` is skipped), so if they ever change the warning would
    be telling users to run something that silently does nothing.
    """
    version_resolver = MagicMock()
    version_resolver.resolve_all.side_effect = lambda values, **kwargs: values
    overrides, _ = parse_cli_overrides(
        [
            "decode.extraContainerConfig.securityContext.capabilities.add=[],"
            "decode.extraContainerConfig.securityContext.runAsUser=1000700000"
        ]
    )
    logger = MagicMock()

    result = RenderPlans(
        template_dir=_REPO / "config/templates/jinja",
        defaults_file=_REPO / "config/templates/values/defaults.yaml",
        scenarios_file=_SCENARIOS / "optimized-baseline.yaml",
        output_dir=tmp_path,
        logger=logger,
        version_resolver=version_resolver,
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
        setup_overrides_by_stack=overrides,
        cli_non_admin=True,
    ).eval()

    assert not result.has_errors, result.to_dict()
    stack_dir = next(path.parent for path in tmp_path.rglob("config.yaml"))
    rendered = yaml.safe_dump(
        yaml.safe_load((stack_dir / "13_ms-values.yaml").read_text(encoding="utf-8"))
    )

    assert "IPC_LOCK" not in rendered
    assert "SYS_RAWIO" not in rendered
    assert "runAsUser: 0" not in rendered
    assert "runAsUser: 1000700000" in rendered


def _decode_security_context(scenario_name: str) -> dict:
    scenario = yaml.safe_load((_SCENARIOS / scenario_name).read_text(encoding="utf-8"))
    stack = scenario["scenario"][0]
    decode = stack["modelservice"]["decode"]
    return decode.get("extraContainerConfig", {}).get("securityContext", {}) or {}


def test_base_optimized_baseline_still_requests_elevation() -> None:
    """Canary: the shipped guide really does hit the path warned about above.

    If this scenario ever stops asking for root, the warning is no longer
    reachable from a stock ``--spec guides/optimized-baseline --non-admin``
    run, and the `--set` remediation it prints no longer applies verbatim.
    """
    security_context = _decode_security_context("optimized-baseline.yaml")

    assert security_context.get("runAsUser") == 0
    assert security_context.get("capabilities", {}).get("add") == [
        "IPC_LOCK",
        "SYS_RAWIO",
    ]
