"""render_plan.py"""

from copy import deepcopy
from pathlib import Path
import yaml

from jinja2 import Environment

from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger


class RenderPlans:
    """
    Render and validate llmdbenchmark stack plans from Jinja2 templates.

    This class orchestrates the full rendering pipeline:
    - Loading defaults and scenario configuration
    - Deep-merging stack overrides
    - Applying optional resource presets
    - Rendering Jinja2 templates to YAML
    - Writing output to some output dir
    - Validating generated YAML files

    Instances are configured with file paths and invoked via `eval()`.
    """

    def __init__(
        self,
        template_file: Path,
        defaults_file: Path,
        scenarios_file: Path,
        output_dir: Path,
        logger=None,
    ):
        self.template_file = template_file
        self.defaults_file = defaults_file
        self.scenarios_file = scenarios_file
        self.output_dir = output_dir

        self.logger = logger or get_logger(
            config.log_dir, verbose=config.verbose, log_name=__name__
        )

    def load_values(self, values_file):
        """Load values from YAML file with full YAML support including anchors and null values."""
        with open(values_file, "r", encoding="utf-8") as f:
            return yaml.full_load(f)

    def deep_merge(self, base, override):
        """
        Deep merge two dictionaries. Override values take precedence.

        Args:
            base: Base dictionary
            override: Dictionary with override values

        Returns:
            Merged dictionary
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

    def apply_resource_preset(self, values):
        """
        Apply resource preset if specified in values.

        If values contains 'resourcePreset' key and 'resourcePresets' exists in defaults,
        merge the preset into decode/prefill configurations.

        Args:
            values: Merged values dictionary

        Returns:
            Values with resource preset applied
        """
        if "resourcePreset" not in values:
            return values

        preset_name = values.get("resourcePreset")
        presets = values.get("resourcePresets", {})

        if preset_name not in presets:
            self.logger.log_warning(
                f"Resource preset '{preset_name}' not found, skipping..."
            )
            return values

        preset = presets[preset_name]
        result = deepcopy(values)

        # Apply preset to decode
        if "decode" in preset:
            result["decode"] = self.deep_merge(
                result.get("decode", {}), preset["decode"]
            )

        # Apply preset to prefill
        if "prefill" in preset:
            result["prefill"] = self.deep_merge(
                result.get("prefill", {}), preset["prefill"]
            )

        self.logger.log_info(f"Applied resource preset: {preset_name}")
        return result

    def render_template(self, template_content, values):
        """Render a Jinja2 template with given values."""

        def indent_filter(text, width=4, first=False):
            """Indent text by specified width."""
            if not text:
                return text
            lines = text.split("\n")
            if first:
                # Indent all lines including first
                return "\n".join(" " * width + line if line else "" for line in lines)
            else:
                # Don't indent first line
                if len(lines) == 1:
                    return text
                return (
                    lines[0]
                    + "\n"
                    + "\n".join(
                        " " * width + line if line else "" for line in lines[1:]
                    )
                )

        def toyaml_filter(value, indent=0, default_flow_style=False):
            """
            Convert Python object to YAML string.

            Args:
                value: Python object to convert
                indent: Number of spaces to indent each line
                default_flow_style: If True, use flow style for collections

            Returns:
                YAML string representation
            """
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

        def is_empty_filter(value):
            """Check if value is empty (None, empty string, empty dict/list)."""
            if value is None:
                return True
            if isinstance(value, str) and not value.strip():
                return True
            if isinstance(value, (dict, list)) and len(value) == 0:
                return True
            return False

        def default_filter(value, default_value):
            """Return default value if value is empty."""
            if is_empty_filter(value):
                return default_value
            return value

        env = Environment(
            autoescape=False,  # Disable autoescape for YAML files
            trim_blocks=True,  # Remove first newline after a block tag
            lstrip_blocks=True,  # Strip leading whitespace from block tags
            keep_trailing_newline=False,  # Don't preserve trailing newlines
        )

        # Add custom filters
        env.filters["indent"] = indent_filter
        env.filters["toyaml"] = toyaml_filter
        env.filters["is_empty"] = is_empty_filter
        env.filters["default_if_empty"] = default_filter

        template = env.from_string(template_content)
        return template.render(**values)

    def split_templates(self, template_file):
        """Split multi-document template file into individual templates."""
        with open(template_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract macros (everything before first ---SPLIT---)
        parts = content.split("\n---SPLIT---\n", 1)
        macros = parts[0] if len(parts) > 1 else ""

        # Split remaining documents by separator
        documents = parts[1].split("\n---SPLIT---\n") if len(parts) > 1 else [content]

        templates = []
        for doc in documents:
            doc = doc.strip()
            if not doc:
                continue

            # Extract filename from comment
            lines = doc.split("\n")
            filename = None
            template_content = []

            for line in lines:
                if line.strip().startswith("# ===") or line.strip().startswith("#"):
                    # Check if this is a filename comment
                    if ".yaml.j2" in line or ".yaml" in line:
                        # Extract filename
                        parts = line.split()
                        for part in parts:
                            if ".yaml" in part:
                                filename = part
                                break
                else:
                    template_content.append(line)

            if filename and template_content:
                # Prepend macros to each template
                full_content = macros + "\n" + "\n".join(template_content)
                templates.append(
                    {
                        "filename": filename.replace(".j2", ""),
                        "content": full_content,
                    }
                )

        return templates

    def _render(self, template_file, defaults_file, scenario_file, output_base_dir):
        """
        Render multiple stacks based on a scenario file.

        Args:
            template_file: Path to the Jinja2 template file
            defaults_file: Path to the defaults YAML file
            scenario_file: Path to the scenario YAML file
            output_base_dir: Base directory for output (each stack gets a subdirectory)
        """
        # Load defaults and scenario
        defaults = self.load_values(defaults_file)
        scenario = self.load_values(scenario_file)

        if "scenario" not in scenario:
            self.logger.log_error(
                "Scenario file must contain a 'scenario' key with a list of stacks",
            )
            return

        stacks = scenario["scenario"]

        if not isinstance(stacks, list):
            self.logger.log_error(
                "'scenario' must be a list of stack configurations",
            )
            return

        self.logger.log_info(f"Processing scenario with {len(stacks)} stack(s)...")
        self.logger.line_break()

        # Create base output directory
        base_path = Path(output_base_dir)
        base_path.mkdir(parents=True, exist_ok=True)

        # Process each stack
        for i, stack in enumerate(stacks, 1):
            if "name" not in stack:
                self.logger.log_warning(
                    f"Stack {i} missing 'name' field, skipping...",
                )
                continue

            stack_name = stack["name"]
            self.logger.log_info(f"[{i}/{len(stacks)}] Processing stack: {stack_name}")
            self.logger.log_info("-" * 60)

            # Create merged values (defaults + stack overrides)
            # Remove 'name' from stack config before merging
            stack_config = {k: v for k, v in stack.items() if k != "name"}
            merged_values = self.deep_merge(defaults, stack_config)

            # Apply resource preset if specified
            merged_values = self.apply_resource_preset(merged_values)

            # Create output directory for this stack
            stack_output_dir = base_path / stack_name
            stack_output_dir.mkdir(parents=True, exist_ok=True)

            # Split and render templates
            templates = self.split_templates(template_file)

            success_count = 0
            error_count = 0

            for template_info in templates:
                filename = template_info["filename"]
                content = template_info["content"]

                try:
                    rendered = self.render_template(content, merged_values)

                    # Strip leading/trailing whitespace
                    rendered = rendered.strip()

                    # Write to output file
                    output_file = stack_output_dir / filename
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(rendered)
                        f.write("\n")  # Add single trailing newline

                    self.logger.log_info(f"Rendered: {filename}", emoji="✅")
                    success_count += 1
                except Exception as e:
                    self.logger.log_error(f"Error rendering {filename}: {e}")
                    error_count += 1
                    continue

            yaml_errors = self.validate_yaml_files(stack_output_dir)
            if yaml_errors:
                self.logger.log_error("YAML validation issues:")
                for err in yaml_errors:
                    self.logger.log_error(f"{err}")

            self.logger.log_info(f"Output: {stack_output_dir}")
            self.logger.log_info(f"Success: {success_count}, Errors: {error_count}")
            self.logger.line_break()

        self.logger.log_info("=" * 60)
        self.logger.log_info(
            f"Scenario rendering complete! Output in: {output_base_dir}", emoji="✅"
        )

    def validate_yaml_files(self, directory):
        """
        Validate all YAML files in a directory.

        Args:
            directory: Path to directory containing YAML files

        Returns:
            List of error messages (empty if all valid)
        """
        errors = []
        for yaml_file in Path(directory).glob("*.yaml"):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    # Use safe_load_all to handle multi-document YAML
                    list(yaml.safe_load_all(f))
            except yaml.YAMLError as e:
                errors.append(f"{yaml_file.name}: {str(e)[:100]}")
        return errors

    def eval(self) -> None:
        self._render(
            self.template_file, self.defaults_file, self.scenarios_file, self.output_dir
        )
