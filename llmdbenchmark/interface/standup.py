"""
standup.py

This module defines the `standup` command for the CLI. It includes argument definitions
and the execution function.
"""

import argparse


def add_subcommands(parser: argparse._SubParsersAction):
    """Add the standup command to the given parser."""
    standup_parser = parser.add_parser(
        "standup", help="Standup and provision model infrastructure."
    )
    standup_parser.add_argument(
        "-s",
        "--step",
        help="Step list (comma-separated values or ranges, e.g. 0,1,5 or 1-7)",
    )
    standup_parser.add_argument(
        "-c", "--scenario", help="Scenario file to source environment variables from"
    )
    standup_parser.add_argument("-m", "--models", help="List of models to be stood up")
    standup_parser.add_argument(
        "-p", "--namespace", help="Namespaces (deploy_namespace,benchmark_namespace)"
    )
    standup_parser.add_argument(
        "-t", "--methods", help="Standup methods (standalone, modelservice)"
    )
    standup_parser.add_argument("-a", "--affinity", help="Kubernetes node affinity")
    standup_parser.add_argument(
        "-b", "--annotations", help="Kubernetes pod annotations"
    )
    standup_parser.add_argument(
        "-r", "--release", help="Modelservice Helm chart release name"
    )
    standup_parser.add_argument(
        "-u", "--wva", help="Enable Workload Variant Autoscaler"
    )
