"""
Benchmark report base class with common methods.
"""

import json
from typing import Any

from pydantic import BaseModel
import yaml


class BenchmarkReport(BaseModel):
    """Common base class for a benchmark report."""

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkReport to dict.

        Returns:
            dict: Defined fields of BenchmarkReport.
        """
        return self.model_dump(
            mode="json",
            exclude_none=True,
            by_alias=True,
        )

    def export_json(self, filename) -> None:
        """Save BenchmarkReport to JSON file.

        Args:
            filename: File to save BenchmarkReport to.
        """
        with open(filename, 'w') as file:
            json.dump(self.dump(), file, indent=2)

    def export_yaml(self, filename) -> None:
        """Save BenchmarkReport to YAML file.

        Args:
            filename: File to save BenchmarkReport to.
        """
        with open(filename, 'w') as file:
            yaml.dump(self.dump(), file, indent=2)

    def get_json_str(self) -> str:
        """Make a JSON string for BenchmarkReport.

        Returns:
            str: JSON string.
        """
        return json.dumps(self.dump(), indent=2)

    def get_yaml_str(self) -> str:
        """Make a YAML string for BenchmarkReport.

        Returns:
            str: YAML string.
        """
        return yaml.dump(self.dump(), indent=2)
