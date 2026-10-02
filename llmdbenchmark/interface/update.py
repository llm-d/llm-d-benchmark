"""CLI definition for the ``update`` subcommand."""

import argparse

from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.standup import add_deploy_arguments
from llmdbenchmark.update import COMPONENT_STEPS, DANGEROUS_COMPONENTS


def add_subcommands(
    parser: argparse._SubParsersAction, parents: list[argparse.ArgumentParser] = []
):
    """Register the ``update`` subcommand and its arguments."""
    update_parser = parser.add_parser(
        Command.UPDATE.value,
        parents=parents,
        description=(
            "The `update` command changes knobs on an already stood-up stack, "
            "re-applying only the components the change affects. A vLLM knob "
            "restarts the serving pods; an EPP knob restarts the endpoint "
            "picker; neither re-downloads model weights nor touches PVCs or "
            "CRDs. The shared gateway and infra releases are re-applied, "
            "which changes nothing when their values did not change. A "
            "smoketest runs afterwards unless --skip-smoketest is passed.\n\n"
            "Pass the change with --set, e.g. "
            "`update -p NS --set decode.replicas=4`.\n\n"
            "IMPORTANT: update re-renders the whole plan from the "
            "specification and scenario. The flags of the original standup "
            "are read back from the in-cluster "
            "llm-d-benchmark-standup-invocation Secret in the -p "
            "namespace and reused, so they do not have to be repeated; "
            "anything passed on this invocation wins over the persisted "
            "value, and only what differs from it counts as a change. Pass "
            "--no-reuse-invocation to ignore the persisted flags, and check "
            "the reuse lines in the log if a knob looks unexpected."
        ),
        help="Update a deployed stack, restarting only affected components.",
    )
    update_parser.add_argument(
        "-s",
        "--step",
        help=(
            "Step list (comma-separated values or ranges, e.g. 6,8 or 6-8). "
            "Overrides the steps inferred from --set."
        ),
    )
    update_parser.add_argument(
        "--component",
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated components to re-apply, bypassing the inference "
            "from --set (union with it when both are given). Known: "
            + ", ".join(sorted(COMPONENT_STEPS))
            + ". The ones that cannot be re-applied in place ("
            + ", ".join(sorted(DANGEROUS_COMPONENTS))
            + ") need --force, however they come into scope."
        ),
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Allow changes that cannot be applied in place: the --set keys "
            "model, storage, downloadJob, namespace, gatewayApiCrd, release, "
            "kustomize, nok8s, a change of -m, -t, -r or --no-pvc, and any "
            "change or -s list that reaches the components "
            + ", ".join(sorted(DANGEROUS_COMPONENTS))
            + ". These re-download weights, recreate PVCs, re-apply "
            "cluster-scoped CRDs or rename every helm release, so the result "
            "may not match a clean standup."
        ),
    )
    update_parser.add_argument(
        "--reuse-invocation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Read the original standup's flags back from the "
            "llm-d-benchmark-standup-invocation Secret and reuse them, so "
            "they need not be repeated (default: on; needs -p). "
            "--no-reuse-invocation renders from this invocation alone, which "
            "reverts any knob the original standup set and this one omits."
        ),
    )
    add_deploy_arguments(update_parser, unset_by_default=True)
