"""
Benchmark report v0.2
"""

from pydantic import ConfigDict

try:
    from benchmark_report import BenchmarkReport
except ImportError:
    from config_explorer.benchmark_report import BenchmarkReport

# BenchmarkReport schema version
VERSION = '0.2'

class BenchmarkReportV02(BenchmarkReport):
    """Base class for a benchmark report."""

    model_config = ConfigDict(
        title="Benchmark Report v0.2",
        extra="forbid", # Do not allow fields that are not part of this schema
        use_attribute_docstrings=True, # Use docstrings for JSON schema
 		populate_by_name=False, # Must use alias name, not internal field name
        validate_assignment=True, # Validate field assignment after init
    )

    version: str = VERSION
    """Version of the schema."""
