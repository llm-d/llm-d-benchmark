"""CLI interface for LLM-D Stack Discovery Tool."""

import logging
import sys
from typing import Optional

import click

from .discovery.utils import kube_connect
from .discovery.tracer import StackTracer
from .output.formatter import OutputFormatter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@click.command()
@click.argument("url", required=True)
@click.option(
    "--kubeconfig",
    "-k",
    type=click.Path(exists=True),
    help="Path to kubeconfig file (defaults to ~/.kube/config)",
)
@click.option(
    "--context",
    "-c",
    help="Kubernetes context to use",
)
@click.option(
    "--output-format",
    "-f",
    type=click.Choice(
        ["json", "yaml", "summary", "native", "native-yaml", "benchmark-report"]
    ),
    default="summary",
    help=(
        "Output format (default: summary). Use 'native' for raw config as JSON,"
        " 'native-yaml' for raw config as YAML, 'benchmark-report' for v0.2 schema."
    ),
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path (defaults to stdout)",
)
@click.option(
    "--filter",
    "filter_type",
    help="Filter components by type (e.g., Pod, Service, vllm)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
def discover(
    url: str,
    kubeconfig: Optional[str],
    context: Optional[str],
    output_format: str,
    output: Optional[str],
    filter_type: Optional[str],
    verbose: bool,
):
    """Discover LLM-D stack configuration from an OpenAI endpoint URL.

    URL should be an OpenAI-compatible endpoint, e.g.:
    https://model.example.com/v1
    """
    # Set logging level
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        # Suppress some noisy loggers
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("kubernetes").setLevel(logging.WARNING)

    try:
        # Connect to Kubernetes
        logger.info("Connecting to Kubernetes cluster...")
        api, k8s_client = kube_connect(kubeconfig, context)

        # Create tracer and discover
        tracer = StackTracer(api, k8s_client)
        result = tracer.trace(url)

        # Format output
        formatter = OutputFormatter()
        output_file = None
        if output:
            output_file = open(  # pylint: disable=consider-using-with
                output, "w", encoding="utf-8"
            )

        try:
            output_str = formatter.format(
                result,
                format_type=output_format,
                output_file=output_file,
                filter_type=filter_type,
            )
        finally:
            if output_file:
                output_file.close()

        # Print to stdout if no output file
        if not output:
            print(output_str)

        # Exit with error if discovery had errors
        if result.errors:
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("Discovery interrupted by user")
        sys.exit(130)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Discovery failed: %s", e)
        if verbose:
            import traceback  # pylint: disable=import-outside-toplevel

            traceback.print_exc()
        sys.exit(1)


def main():
    """Main entry point."""
    discover()  # pylint: disable=no-value-for-parameter


if __name__ == "__main__":
    main()
