"""
llmdbenchmark.parser.render_plans

Provides the RenderPlans class for rendering and validating llmdbenchmark stack plans.

This module handles:
- Parsing Jinja2 templates into individual stack documents.
- Rendering templates to YAML with defaults, stack overrides, and optional resource presets.
- Writing rendered YAML files to an output directory.
- Validating YAML content and filesystem paths.
- Tracking errors at both global and per-stack levels using the RenderResult and StackErrors dataclasses.
"""

from copy import deepcopy
from pathlib import Path
from typing import Optional, Any
import yaml

from jinja2 import Environment, TemplateSyntaxError, UndefinedError

from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger
from llmdbenchmark.parser.render_result import StackErrors, RenderResult


class RenderPlans:
    """
    Render and validate llmdbenchmark stack plans from Jinja2 templates.

    This class orchestrates the end-to-end rendering pipeline for benchmark stack plans,
    including template parsing, YAML rendering, and validation. It tracks errors at both
    the global level (e.g., file loading issues) and per-stack level (rendering or YAML issues).

    Workflow:
        1. Load defaults and scenario configuration YAML files.
        2. Validate that the scenario file contains a list of stack configurations.
        3. Parse the Jinja2 template file into individual template documents.
        4. For each stack:
            a. Merge stack overrides with defaults.
            b. Apply optional resource presets.
            c. Render all templates to YAML files.
            d. Write rendered YAML to the output directory.
            e. Validate the YAML files.
            f. Track errors and successful output paths.
        5. Return a RenderResult object containing structured error information and rendered paths.

    Attributes:
        template_file (Path): Path to the Jinja2 template file.
        defaults_file (Path): Path to the defaults YAML file.
        scenarios_file (Path): Path to the scenario YAML file.
        output_dir (Path): Base output directory for rendered stack plans.
        logger: Logger instance for debug/info/error messages.
        _template_cache (Optional[list[dict]]): Cached parsed templates to avoid re-parsing.
        _jinja_env (Optional[Environment]): Cached Jinja2 environment with custom filters.

    Methods:
        eval() -> RenderResult:
            Execute the full rendering pipeline and return structured results including errors.
        _process_stack(...):
            Internal method to process an individual stack configuration.
        deep_merge(base: dict, override: dict) -> dict:
            Deep merge two dictionaries, with override values taking precedence.
        _apply_resource_preset(values: dict) -> dict:
            Apply resource preset values if specified in the stack configuration.
        _split_templates() -> list[dict]:
            Split the multi-document Jinja2 template into individual template documents.
        _render_template(template_content: str, values: dict) -> str:
            Render a single Jinja2 template with provided values.
        _validate_yaml_files(directory: Path) -> list[str]:
            Validate all YAML files in a directory and return error messages.
        _get_jinja_env() -> Environment:
            Create or return a cached Jinja2 environment with custom filters.

    """

    SPLIT_MARKER = "\n---SPLIT---\n"

    def __init__(
        self,
        template_file: Path,
        defaults_file: Path,
        scenarios_file: Path,
        output_dir: Path,
        logger=None,
    ):
        self.template_file = Path(template_file)
        self.defaults_file = Path(defaults_file)
        self.scenarios_file = Path(scenarios_file)
        self.output_dir = Path(output_dir)

        self.logger = logger or get_logger(
            config.log_dir, verbose=config.verbose, log_name=__name__
        )

        # Cache for parsed templates (avoid re-parsing on multiple evals)
        self._template_cache: Optional[list[dict]] = None

        # Jinja2 environment (reusable)
        self._jinja_env: Optional[Environment] = None

    def _get_jinja_env(self) -> Environment:
        """Get or create the Jinja2 environment with custom filters."""
        if self._jinja_env is not None:
            return self._jinja_env

        env = Environment(
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

        # Register custom filters
        env.filters["indent"] = self._indent_filter
        env.filters["toyaml"] = self._toyaml_filter
        env.filters["is_empty"] = self._is_empty_filter
        env.filters["default_if_empty"] = self._default_if_empty_filter

        self._jinja_env = env
        return env

    @staticmethod
    def _indent_filter(text: str, width: int = 4, first: bool = False) -> str:
        """Indent text by specified width."""
        if not text:
            return text
        lines = text.split("\n")
        if first:
            return "\n".join(" " * width + line if line else "" for line in lines)
        if len(lines) == 1:
            return text
        return (
            lines[0]
            + "\n"
            + "\n".join(" " * width + line if line else "" for line in lines[1:])
        )

    @staticmethod
    def _toyaml_filter(
        value: Any, indent: int = 0, default_flow_style: bool = False
    ) -> str:
        """Convert Python object to YAML string."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)) and len(value) == 0:
            return ""

        result = yaml.dump(
            value, default_flow_style=default_flow_style, allow_unicode=True
        ).rstrip()

        if indent > 0:
            lines = result.split("\n")
            return "\n".join(
                " " * indent + line if line.strip() else line for line in lines
            )
        return result

    @staticmethod
    def _is_empty_filter(value: Any) -> bool:
        """Check if value is empty (None, empty string, empty dict/list)."""
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        if isinstance(value, (dict, list)) and len(value) == 0:
            return True
        return False

    @staticmethod
    def _default_if_empty_filter(value: Any, default_value: Any) -> Any:
        """Return default value if value is empty."""
        if RenderPlans._is_empty_filter(value):
            return default_value
        return value

    def _load_yaml(self, yaml_file: Path) -> dict:
        """
        Load values from YAML file with full YAML support.

        Args:
            yaml_file: Path to YAML file

        Returns:
            Parsed YAML as dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
            yaml.YAMLError: If YAML is invalid
        """
        if not yaml_file.exists():
            raise FileNotFoundError(f"YAML file not found: {yaml_file}")

        with open(yaml_file, "r", encoding="utf-8") as f:
            return yaml.full_load(f)

    def deep_merge(self, base: dict, override: dict) -> dict:
        """
        Deep merge two dictionaries. Override values take precedence.

        Args:
            base: Base dictionary
            override: Dictionary with override values

        Returns:
            New merged dictionary (inputs are not modified)
        """
        result = deepcopy(base)

        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self.deep_merge(result[key], value)
            else:
                result[key] = deepcopy(value)

        return result

    def _apply_resource_preset(self, values: dict) -> dict:
        """
        Apply resource preset if specified in values.

        If values contains 'resourcePreset' key and 'resourcePresets' exists,
        merge the preset into decode/prefill configurations.

        Args:
            values: Merged values dictionary

        Returns:
            Values with resource preset applied
        """
        preset_name = values.get("resourcePreset")
        if not preset_name:
            return values

        presets = values.get("resourcePresets", {})
        if preset_name not in presets:
            self.logger.log_warning(
                f"Resource preset '{preset_name}' not found, skipping..."
            )
            return values

        preset = presets[preset_name]
        result = deepcopy(values)

        # Apply preset to decode and prefill
        for component in ("decode", "prefill"):
            if component in preset:
                result[component] = self.deep_merge(
                    result.get(component, {}), preset[component]
                )

        self.logger.log_info(f"Applied resource preset: {preset_name}")
        return result

    def _extract_filename_from_line(self, line: str) -> Optional[str]:
        """
        Extract YAML filename from a comment line.

        Args:
            line: A line that may contain a filename like "# 01_foo.yaml.j2"

        Returns:
            Filename without .j2 extension, or None if not found
        """
        if ".yaml" not in line:
            return None

        for part in line.split():
            if ".yaml" in part:
                return part.replace(".j2", "")

        return None

    def _parse_template_document(self, doc: str) -> Optional[tuple[str, list[str]]]:
        """
        Parse a single template document to extract filename and content lines.

        Args:
            doc: Raw template document string

        Returns:
            Tuple of (filename, content_lines) or None if invalid
        """
        doc = doc.strip()
        if not doc:
            return None

        filename = None
        content_lines = []

        for line in doc.split("\n"):
            if line.strip().startswith("#"):
                # Try to extract filename from comment
                if filename is None:
                    filename = self._extract_filename_from_line(line)
            else:
                content_lines.append(line)

        if filename and content_lines:
            return filename, content_lines

        return None

    def _split_templates(self) -> list[dict]:
        """
        Split multi-document template file into individual templates.

        Returns:
            List of dicts with 'filename' and 'content' keys
        """
        if self._template_cache is not None:
            return self._template_cache

        with open(self.template_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract macros (everything before first ---SPLIT---)
        parts = content.split(self.SPLIT_MARKER, 1)
        macros = parts[0] if len(parts) > 1 else ""
        documents = parts[1].split(self.SPLIT_MARKER) if len(parts) > 1 else [content]

        templates = []
        for doc in documents:
            parsed = self._parse_template_document(doc)
            if parsed:
                filename, content_lines = parsed
                templates.append(
                    {
                        "filename": filename,
                        "content": macros + "\n" + "\n".join(content_lines),
                    }
                )

        self._template_cache = templates
        return templates

    def _render_template(self, template_content: str, values: dict) -> str:
        """
        Render a Jinja2 template with given values.

        Args:
            template_content: Jinja2 template string
            values: Dictionary of values to render

        Returns:
            Rendered string

        Raises:
            TemplateSyntaxError: If template has syntax errors
            UndefinedError: If template references undefined variables
        """
        env = self._get_jinja_env()
        template = env.from_string(template_content)
        return template.render(**values)

    def _validate_yaml_files(self, directory: Path) -> list[str]:
        """
        Validate all YAML files in a directory.

        Args:
            directory: Path to directory containing YAML files

        Returns:
            List of error messages (empty if all valid)
        """
        errors = []
        for yaml_file in directory.glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    list(yaml.safe_load_all(f))
            except yaml.YAMLError as e:
                errors.append(f"{yaml_file.name}: {str(e)[:100]}")
        return errors

    def _process_stack(
        self,
        stack: dict,
        stack_index: int,
        total_stacks: int,
        defaults: dict,
        templates: list[dict],
        base_path: Path,
        result: RenderResult,
    ) -> None:
        """
        Process a single stack configuration.

        Args:
            stack: Stack configuration dictionary
            stack_index: 1-based index of this stack
            total_stacks: Total number of stacks
            defaults: Default values dictionary
            templates: List of parsed templates
            base_path: Base output directory
            result: RenderResult to update with errors/paths
        """
        if "name" not in stack:
            msg = f"Stack {stack_index} missing 'name' field, skipping"
            self.logger.log_warning(msg)
            result.global_errors.append(msg)
            return

        stack_name = stack["name"]
        self.logger.log_info(
            f"[{stack_index}/{total_stacks}] Processing stack: {stack_name}"
        )

        # Initialize error tracking for this stack
        stack_errors = StackErrors()
        result.stacks[stack_name] = stack_errors

        # Merge defaults with stack overrides (exclude 'name')
        stack_config = {k: v for k, v in stack.items() if k != "name"}
        merged_values = self.deep_merge(defaults, stack_config)

        # Apply resource preset if specified
        merged_values = self._apply_resource_preset(merged_values)

        # Create output directory
        stack_output_dir = base_path / stack_name
        stack_output_dir.mkdir(parents=True, exist_ok=True)

        success_count = 0
        error_count = 0

        for template_info in templates:
            filename = template_info["filename"]
            content = template_info["content"]

            try:
                rendered = self._render_template(content, merged_values).strip()

                output_file = stack_output_dir / filename
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(rendered)
                    f.write("\n")

                self.logger.log_info(f"Rendered: {filename}", emoji="✅")
                success_count += 1

            except (TemplateSyntaxError, UndefinedError) as e:
                msg = f"{filename}: {e}"
                self.logger.log_error(f"Template error in {filename}: {e}")
                stack_errors.render_errors.append(msg)
                error_count += 1

            except Exception as e:
                msg = f"{filename}: {e}"
                self.logger.log_error(f"Error rendering {filename}: {e}")
                stack_errors.render_errors.append(msg)
                error_count += 1

        # Validate rendered YAML
        yaml_errors = self._validate_yaml_files(stack_output_dir)
        if yaml_errors:
            self.logger.log_error("YAML validation issues:")
            for err in yaml_errors:
                self.logger.log_error(f"  {err}")
                stack_errors.yaml_errors.append(err)

        # Track successful output path
        if not stack_errors.has_errors:
            result.rendered_paths.append(stack_output_dir)

        self.logger.log_info(f"Output: {stack_output_dir}")
        self.logger.log_info(f"Success: {success_count}, Errors: {error_count}")
        self.logger.line_break()

    def eval(self) -> RenderResult:
        """
        Execute the rendering pipeline.

        Returns:
            RenderResult with error tracking and rendered paths
        """
        result = RenderResult()

        # Load defaults
        try:
            defaults = self._load_yaml(self.defaults_file)
        except Exception as e:
            msg = f"Failed to load defaults file: {e}"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

        # Load scenario
        try:
            scenario = self._load_yaml(self.scenarios_file)
        except Exception as e:
            msg = f"Failed to load scenario file: {e}"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

        # Validate scenario structure
        if "scenario" not in scenario:
            msg = "Scenario file must contain a 'scenario' key with a list of stacks"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

        stacks = scenario["scenario"]
        if not isinstance(stacks, list):
            msg = "'scenario' must be a list of stack configurations"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

        self.logger.log_info(f"Processing scenario with {len(stacks)} stack(s)...")
        self.logger.line_break()

        # Parse templates once
        try:
            templates = self._split_templates()
        except Exception as e:
            msg = f"Failed to parse template file: {e}"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Process each stack
        for i, stack in enumerate(stacks, 1):
            self._process_stack(
                stack=stack,
                stack_index=i,
                total_stacks=len(stacks),
                defaults=defaults,
                templates=templates,
                base_path=self.output_dir,
                result=result,
            )

        self.logger.log_info(
            f"Scenario rendering complete! Output in: {self.output_dir}"
        )

        return result
