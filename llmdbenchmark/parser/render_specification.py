"""Render and validate the specification YAML that defines template/scenario locations."""

from pathlib import Path
from typing import Any, Dict, List, Union

import json
import yaml

from jinja2 import Environment, TemplateError as JinjaTemplateError

from llmdbenchmark.config import config
from llmdbenchmark.utilities.os.filesystem import (
    directory_exists_and_nonempty,
    file_exists_and_nonzero,
    get_absolute_path,
)
from llmdbenchmark.logging.logger import get_logger
from llmdbenchmark.exceptions.exceptions import TemplateError, ConfigurationError

# Keys every consumer of the returned config dict indexes unconditionally as
# `<key>["path"]` (see cli.py dispatch_cli and _render_plans_for_experiment).
# Missing any of them is a config mistake, not a runtime KeyError.
_REQUIRED_KEYS = ("template_dir", "values_file", "scenario_file")


class RenderSpecification:  # pylint: disable=too-few-public-methods
    """
    Render a Jinja specification file, write the resulting YAML,
    parse it into a dictionary, and validate filesystem paths.
    """

    def __init__(
        self,
        specification_file: Path,
        base_dir: Path | None = None,
        logger=None,
    ):
        self.specification_file = specification_file
        self.base_dir = base_dir

        self.plan_dir = config.plan_dir

        self.logger = logger or get_logger(
            config.log_dir, verbose=config.verbose, log_name=__name__
        )

        self.errors: List[str] = []

    def _render(self) -> str:
        """Render the specification Jinja2 template and write the YAML output."""
        try:
            env = Environment(
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True,
            )

            template = env.from_string(self.specification_file.read_text())

            render_kwargs = {}
            if self.base_dir:
                render_kwargs["base_dir"] = str(self.base_dir)
            rendered_yaml = template.render(**render_kwargs)

            output_path = self.plan_dir / f"{self.specification_file.stem}"
            yaml_suffix = ".yaml"
            if output_path.suffix != yaml_suffix:
                output_path = output_path.with_suffix(yaml_suffix)
            output_path.write_text(rendered_yaml)
            return rendered_yaml

        except JinjaTemplateError as exc:
            raise TemplateError(
                message="Failed to render specification template",
                step="Validate YAML Specification Template",
                template_file=str(self.specification_file),
                context={"jinja_error": str(exc)},
            ) from exc

        except OSError as exc:
            raise TemplateError(
                message="Failed to read or write specification template",
                step="Render YAML Specification",
                template_file=str(self.specification_file),
                context={"os_error": str(exc)},
            ) from exc

    def _parse(self, rendered_yaml: str) -> Dict[str, Any]:
        """Parse rendered YAML into a dict."""
        try:
            return yaml.safe_load(rendered_yaml)
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                message="Rendered specification is not valid YAML",
                step="Render YAML Specification",
                config_file=f"{self.specification_file.stem}.yaml",
                context={"yaml_error": str(exc)},
            ) from exc

    def _check_required(self, config_dict: Any) -> None:
        """Validate that the specification defines every required key as `{path: ...}`."""
        node = config_dict if isinstance(config_dict, dict) else {}
        missing = [
            key
            for key in _REQUIRED_KEYS
            if not isinstance(node.get(key), dict) or "path" not in node[key]
        ]
        if not missing:
            return

        if "scenario" in node:
            hint = (
                "this looks like a scenario file, not a specification; pass a "
                "specification instead, for example --spec guides/nok8s"
            )
        else:
            hint = (
                "specifications live under config/specification/ and must define "
                "each required key as '<key>:' followed by 'path: <location>'"
            )

        raise ConfigurationError(
            message=f"Specification is missing required keys: {', '.join(missing)}",
            step="Validate required specification keys",
            config_file=f"{self.specification_file.stem}.yaml",
            context={"hint": hint},
        )

    def _precheck(self, node: Any, prefix: str = "") -> None:
        """Recursively validate filesystem paths referenced in the config."""
        if isinstance(node, dict):
            if set(node.keys()) == {"path"}:
                self._check_path(node["path"], f"{prefix}.path" if prefix else "path")
                return

            for key, value in node.items():
                new_prefix = f"{prefix}.{key}" if prefix else key
                self._precheck(value, new_prefix)

        elif isinstance(node, str):
            self._check_path(node, prefix)

    def _check_path(self, value: Union[str, Path], label: str) -> None:
        """Validate that a path exists and is non-empty."""
        try:
            abs_path = get_absolute_path(value)
        except Exception as exc:
            raise ConfigurationError(
                message="Invalid filesystem path",
                step="Validate filesystem path",
                invalid_key=label,
                context={"path": value, "error": str(exc)},
            ) from exc

        if abs_path.is_dir():
            if not directory_exists_and_nonempty(abs_path):
                raise ConfigurationError(
                    message="Directory does not exist or is empty",
                    step="Validate Directory exists or is non empty",
                    invalid_key=label,
                    context={"path": str(abs_path)},
                )

        elif abs_path.is_file():
            if not file_exists_and_nonzero(abs_path):
                raise ConfigurationError(
                    message="File does not exist or is empty",
                    step="Validate file exists and is not empty",
                    invalid_key=label,
                    context={"path": str(abs_path)},
                )

        else:
            raise ConfigurationError(
                message="Path does not exist",
                step="Validate path exists",
                invalid_key=label,
                context={"path": str(abs_path)},
            )

    def eval(self) -> Dict[str, Any]:
        """Render, parse, and validate the specification. Returns the config dict."""
        rendered_yaml = self._render()
        config_dict = self._parse(rendered_yaml)
        self._check_required(config_dict)
        self._precheck(config_dict)

        _json_dump = json.dumps(config_dict, indent=2)
        self.logger.log_debug(
            f"Rendered Specification File\n {_json_dump}",
        )

        return config_dict
