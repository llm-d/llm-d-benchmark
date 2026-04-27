"""CLI definition for the ``teardown`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env


def add_subcommands(parser: argparse._SubParsersAction):
    """Register the ``teardown`` subcommand and its arguments."""
    teardown_parser = parser.add_parser(
        Command.TEARDOWN.value,
        description=(
            "The `teardown` command removes resources deployed by a previous standup. "
            "It uninstalls Helm releases, deletes namespaced resources, and optionally "
            "removes cluster-scoped roles. Use --deep for a full namespace wipe."
        ),
        help="Tear down a previously deployed llm-d stack.",
    )
    teardown_parser.add_argument(
        "-s",
        "--step",
        help="Step list (comma-separated values or ranges, e.g. 0,1,3 or 0-4).",
    )
    teardown_parser.add_argument(
        "-m", "--models",
        default=env("LLMDBENCH_MODELS"),
        help="Model that was deployed (used for resource name resolution).",
    )
    teardown_parser.add_argument(
        "-t", "--methods",
        default=env("LLMDBENCH_METHODS"),
        help="Deployment methods to tear down (standalone, modelservice, fma).",
    )
    teardown_parser.add_argument(
        "-r", "--release",
        default=env("LLMDBENCH_RELEASE", "llmdbench"),
        help="Modelservice Helm chart release name (default: llmdbench).",
    )
    teardown_parser.add_argument(
        "-d", "--deep",
        action="store_true",
        help="Deep cleaning: delete ALL resources in both namespaces.",
    )
    teardown_parser.add_argument(
        "-p", "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Comma-separated namespaces to tear down (model,harness). "
        "Overrides namespace from the plan config. If only one namespace is "
        "provided, it is used for both model and harness.",
    )
    teardown_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file for kubectl/helm/helmfile commands.",
    )
    teardown_parser.add_argument(
        "--stack",
        default=env("LLMDBENCH_STACK"),
        help=(
            "Comma-separated list of stack names to restrict execution to. "
            "Useful for removing one pool from a multi-stack scenario while "
            "leaving siblings in place."
        ),
    )
