"""Render Jinja2 templates into per-stack YAML plans.

Loads templates, merges defaults with scenario overrides, resolves
versions and cluster resources, and writes validated YAML to the output dir.
"""

import base64
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Optional, Any
import yaml

from jinja2 import Environment, TemplateSyntaxError, UndefinedError

from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger
from llmdbenchmark.parser.render_result import StackErrors, RenderResult


class RenderPlans:
    """Render and validate llmdbenchmark stack plans from Jinja2 templates.

    Templates prefixed with ``_`` are treated as macros/partials and not
    rendered directly. All others are rendered per stack with merged values.
    """

    # Prefix for partial/macro files (not rendered directly)
    PARTIAL_PREFIX = "_"

    # Default namespace when "auto" is specified (matches original bash: llmdbench)
    DEFAULT_NAMESPACE = "llmdbench"

    def __init__(
        self,
        template_dir: Path,
        defaults_file: Path,
        scenarios_file: Path,
        output_dir: Path,
        logger=None,
        version_resolver=None,
        cluster_resource_resolver=None,
        cli_namespace: str | None = None,
        cli_model: str | None = None,
        cli_methods: str | None = None,
        cli_monitoring: bool = False,
        setup_overrides: dict | None = None,
    ):
        self.template_dir = Path(template_dir)
        self.defaults_file = Path(defaults_file)
        self.scenarios_file = Path(scenarios_file)
        self.output_dir = Path(output_dir)
        self.version_resolver = version_resolver
        self.cluster_resource_resolver = cluster_resource_resolver
        self.cli_namespace = cli_namespace
        self.cli_model = cli_model
        self.cli_methods = cli_methods
        self.cli_monitoring = cli_monitoring
        self.setup_overrides = setup_overrides

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
        env.filters["tojson"] = self._tojson_filter
        env.filters["is_empty"] = self._is_empty_filter
        env.filters["default_if_empty"] = self._default_if_empty_filter
        env.filters["b64pad"] = self._b64pad_filter
        env.filters["b64encode"] = self._b64encode_filter
        env.filters["model_id_label"] = self._model_id_label_filter

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
    def _tojson_filter(value: Any) -> str:
        """Convert Python object to compact JSON string."""
        if value is None:
            return "null"
        return json.dumps(value, separators=(",", ":"))

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

    @staticmethod
    def _b64pad_filter(value: str) -> str:
        """Ensure a base64 string has proper padding.

        Base64 strings must have length divisible by 4. If not,
        append '=' characters to reach the next multiple of 4.
        This fixes 'illegal base64 data' errors from Kubernetes.
        """
        if not value or not isinstance(value, str):
            return value
        value = value.strip()
        # Add padding to make length a multiple of 4
        remainder = len(value) % 4
        if remainder:
            value += "=" * (4 - remainder)
        return value

    @staticmethod
    def _b64encode_filter(value: str) -> str:
        """Base64-encode a plain-text string.

        Useful for creating Kubernetes Secret data fields from plain text.
        """
        if not value or not isinstance(value, str):
            return value
        return base64.b64encode(value.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _model_id_label_filter(model_name: str, namespace: str = "") -> str:
        """Generate a hashed model ID label matching the bash implementation.

        Takes a model name like 'Qwen/Qwen3-32B' and a namespace, produces
        a DNS-safe label in the format: {first8}-{hash8}-{last8}.

        This matches the bash model_attribute() function in setup/functions.py.
        """

        if not model_name:
            return model_name

        model_id = model_name.replace("/", "-").replace(".", "-")
        hash_input = f"{namespace}/{model_id}" if namespace else model_id
        digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        label = f"{model_id[:8]}-{digest[:8]}-{model_id[-8:]}"
        return label.lower()

    def _load_yaml(self, yaml_file: Path) -> dict:
        """Load and parse a YAML file, raising on missing file or invalid syntax."""
        if not yaml_file.exists():
            raise FileNotFoundError(f"YAML file not found: {yaml_file}")

        with open(yaml_file, "r", encoding="utf-8") as f:
            return yaml.full_load(f)

    def deep_merge(self, base: dict, override: dict) -> dict:
        """Deep-merge two dicts; override values take precedence. Returns a new dict."""
        result = deepcopy(base)

        for key, value in override.items():
            if value is None:
                continue  # YAML key with no value -- don't clobber defaults
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
        """Merge the named resource preset into decode/prefill configs if specified."""
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

    def _resolve_namespace(self, values: dict) -> dict:
        """Resolve namespace config from CLI override or ``"auto"`` default.

        Handles comma-separated ``deploy,harness,wva`` from ``--namespace``.
        """
        result = deepcopy(values)
        ns_config = result.get("namespace", {})
        current_name = ns_config.get("name", "auto")

        if self.cli_namespace:
            parts = [p.strip() for p in self.cli_namespace.split(",")]
            deploy_ns = parts[0] if parts else current_name
            harness_ns = parts[1] if len(parts) > 1 and parts[1] else deploy_ns
            wva_ns = parts[2] if len(parts) > 2 and parts[2] else deploy_ns

            if deploy_ns == "auto":
                deploy_ns = self.DEFAULT_NAMESPACE
            if harness_ns == "auto":
                harness_ns = deploy_ns
            if wva_ns == "auto":
                wva_ns = deploy_ns

            ns_config["name"] = deploy_ns
            result["namespace"] = ns_config

            gw_config = result.get("gateway", {})
            if gw_config.get("namespace") in ("auto", self.DEFAULT_NAMESPACE, ""):
                gw_config["namespace"] = deploy_ns
                result["gateway"] = gw_config

            harness_config = result.get("harness", {})
            harness_config["namespace"] = harness_ns
            result["harness"] = harness_config

            wva_config = result.get("wva", {})
            wva_config["namespace"] = wva_ns
            result["wva"] = wva_config

            self.logger.log_info(
                f"Namespace from CLI: deploy={deploy_ns}, "
                f"harness={harness_ns}, wva={wva_ns}"
            )
        elif current_name == "auto":
            ns_config["name"] = self.DEFAULT_NAMESPACE
            result["namespace"] = ns_config

            gw_config = result.get("gateway", {})
            if gw_config.get("namespace") in ("auto", self.DEFAULT_NAMESPACE, ""):
                gw_config["namespace"] = self.DEFAULT_NAMESPACE
                result["gateway"] = gw_config

            self.logger.log_info(
                f'Namespace "auto" resolved to "{self.DEFAULT_NAMESPACE}"'
            )

        return result

    @staticmethod
    def _generate_short_name(model_id: str, namespace: str = "llmdbench") -> str:
        """Generate a K8s-safe short name from a HuggingFace model ID.

        Follows the bash reference pattern::

            {first_8_chars}-{sha256_first_8}-{last_8_chars}

        Where *chars* come from the normalised model ID (``/`` to ``-``,
        ``.`` to ``-``).  The hash is the SHA-256 of
        ``{namespace}/{normalised_model_id}``.

        The result is lowercased so it is valid as a K8s resource name
        (DNS subdomain: ``[a-z0-9-]``).
        """
        normalised = model_id.replace("/", "-").replace(".", "-")
        hash_input = f"{namespace}/{normalised}"
        digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        first8 = normalised[:8]
        last8 = normalised[-8:]
        hash8 = digest[:8]
        return f"{first8}-{hash8}-{last8}".lower()

    def _resolve_model(self, values: dict) -> dict:
        """Resolve model configuration from CLI ``--models`` override.

        When the user passes ``-m <model>`` on the command line the model
        fields in the merged values dict are updated:

        - ``model.name`` -- the HuggingFace model ID
        - ``model.huggingfaceId`` -- same as name
        - ``model.path`` -- ``models/<model_id>``
        - ``model.shortName`` -- auto-generated K8s-safe label

        The ``shortName`` is derived from the model ID and the already-
        resolved namespace (``_resolve_namespace`` must run first).
        """
        if not self.cli_model:
            return values

        result = deepcopy(values)
        model_config = result.get("model", {})

        model_id = self.cli_model
        model_config["name"] = model_id
        model_config["huggingfaceId"] = model_id
        model_config["path"] = f"models/{model_id}"

        # Derive short name using the already-resolved namespace
        namespace = result.get("namespace", {}).get("name", self.DEFAULT_NAMESPACE)
        model_config["shortName"] = self._generate_short_name(model_id, namespace)

        result["model"] = model_config

        self.logger.log_info(
            f"Model from CLI: {model_id} " f"(shortName={model_config['shortName']})"
        )

        return result

    def _warn_custom_command_conflicts(self, values: dict) -> None:
        """Warn when CLI overrides won't propagate into hardcoded customCommands.

        customCommand is a verbatim string -- CLI flags like --models only
        update the config dict (model.name, etc.) but cannot modify the
        hardcoded values inside customCommand.  Emit a warning so users
        know to update the customCommand manually.
        """
        if not self.cli_model:
            return

        for role in ("decode", "prefill"):
            cmd = (
                values.get(role, {})
                .get("vllm", {})
                .get("customCommand")
            )
            if cmd:
                self.logger.log_warning(
                    f"CLI --models override ({self.cli_model}) will not "
                    f"propagate into {role}.vllm.customCommand. "
                    f"Update the customCommand in your scenario to match, "
                    f"or remove customCommand to use the auto-generated command."
                )

    def _resolve_monitoring(self, values: dict) -> dict:
        """Enable PodMonitor and metrics scraping when ``--monitoring`` is set.

        Matches the bash ``-f/--monitoring`` flag which sets:
        - ``LLMDBENCH_VLLM_MONITORING_PODMONITOR_ENABLED=true``
        - ``LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED=true``
        """
        if not self.cli_monitoring:
            return values

        result = deepcopy(values)

        monitoring_config = result.setdefault("monitoring", {})
        podmonitor_config = monitoring_config.setdefault("podmonitor", {})
        podmonitor_config["enabled"] = True
        monitoring_config["metricsScrapeEnabled"] = True

        self.logger.log_info(
            "Monitoring enabled from CLI: PodMonitor + metrics scraping"
        )
        return result

    def _resolve_deploy_method(self, values: dict) -> dict:
        """Override deploy method based on CLI ``--methods`` flag.

        Accepts ``--methods standalone`` or ``--methods modelservice``.
        Only one method may be active at a time.

        Without ``--methods``, the scenario YAML value is used as-is.
        """
        if not self.cli_methods:
            return values

        result = deepcopy(values)
        methods = [m.strip() for m in self.cli_methods.split(",")]

        if "standalone" in methods and "modelservice" in methods:
            self.logger.log_warning(
                "Cannot enable both standalone and modelservice -- "
                "choose one. Using modelservice."
            )
            methods = ["modelservice"]

        standalone_config = result.setdefault("standalone", {})
        modelservice_config = result.setdefault("modelservice", {})

        if "standalone" in methods:
            standalone_config["enabled"] = True
            modelservice_config["enabled"] = False
            self.logger.log_info("Deploy method from CLI: standalone")
        elif "modelservice" in methods:
            standalone_config["enabled"] = False
            modelservice_config["enabled"] = True
            self.logger.log_info("Deploy method from CLI: modelservice")

        return result

    def _log_image_overrides(self, values: dict) -> None:
        """Log images that have been explicitly set (not 'auto').

        Called before version resolution so users can see which images
        were pinned by the scenario or CLI rather than auto-resolved.
        """
        images = values.get("images", {})
        for key, img in images.items():
            if isinstance(img, dict):
                tag = img.get("tag", "auto")
                repo = img.get("repository", "")
                if tag and tag != "auto" and repo:
                    self.logger.log_info(
                        f"Image override: {key} pinned to {repo}:{tag}"
                    )

        standalone_img = values.get("standalone", {}).get("image", {})
        if isinstance(standalone_img, dict):
            tag = standalone_img.get("tag", "auto")
            repo = standalone_img.get("repository", "")
            if tag and tag != "auto" and repo:
                self.logger.log_info(
                    f"Image override: standalone pinned to {repo}:{tag}"
                )

    # Sentinel values indicating no real HF token has been configured
    def _resolve_model_id_label(self, values: dict) -> dict:
        """Compute the hashed model ID label and inject it into the config.

        Matches the bash model_attribute() function: takes the model name,
        replaces / and . with -, then builds {first8}-{sha256_8}-{last8}.
        The hash input includes the namespace for uniqueness.
        """
        model = values.get("model", {})
        model_name = model.get("name", "")
        namespace = values.get("namespace", {}).get("name", "")

        if model_name:
            model_id = model_name.replace("/", "-").replace(".", "-")
            hash_input = f"{namespace}/{model_id}" if namespace else model_id
            digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
            label = f"{model_id[:8]}-{digest[:8]}-{model_id[-8:]}"
            values["model_id_label"] = label.lower()
        else:
            values["model_id_label"] = model.get("shortName", "")

        return values

    _HF_TOKEN_SENTINELS = {"REPLACE_TOKEN", "REPLACE_TOKEN_B64", ""}

    def _resolve_hf_token(self, values: dict) -> dict:
        """Auto-detect HuggingFace token and set huggingface.enabled.

        When the configured ``huggingface.token`` is still a sentinel
        value (``REPLACE_TOKEN`` or empty), this method checks the
        following environment variables in order:

        1. ``HF_TOKEN``
        2. ``HUGGING_FACE_HUB_TOKEN``

        If a token is found, it is injected into the values dict along
        with its base64-encoded form so that rendered K8s Secret YAMLs
        work correctly.

        Sets ``huggingface.enabled`` to control whether HF token secrets
        and auth are rendered. Public models work without a token --
        the secret and auth blocks are skipped entirely. Gated models
        without a token cause an immediate error.
        """
        result = deepcopy(values)
        hf_config = result.get("huggingface", {})
        current_token = hf_config.get("token", "")

        # Only auto-detect if the current token is a sentinel / empty
        if current_token and current_token not in self._HF_TOKEN_SENTINELS:
            hf_config["enabled"] = True
            result["huggingface"] = hf_config
            return result

        # Check environment variables (order matches HuggingFace SDK convention)
        env_token = os.environ.get("HF_TOKEN") or os.environ.get(
            "HUGGING_FACE_HUB_TOKEN"
        )
        if not env_token:
            # No token available -- disable HF secret/auth rendering.
            # Public models will work fine; gated models are caught at
            # standup time by the model access check.
            hf_config["enabled"] = False
            hf_config["token"] = ""
            hf_config["tokenBase64"] = ""
            result["huggingface"] = hf_config
            self.logger.log_info(
                "No HuggingFace token found -- HF secret will not be created. "
                "Public models will work; gated models will fail at standup.",
                emoji="ℹ️",
            )
            return result

        # Inject the token and its base64-encoded form
        hf_config["token"] = env_token
        hf_config["tokenBase64"] = base64.b64encode(env_token.encode("utf-8")).decode(
            "utf-8"
        )
        hf_config["enabled"] = True
        result["huggingface"] = hf_config

        self.logger.log_info(
            "HuggingFace token detected from environment "
            f"(hf_{'*' * 4}…{env_token[-4:]})",
            emoji="🔑",
        )

        return result

    def _load_templates(self) -> list[dict]:
        """Load .j2 files from the template dir, prepending shared macros."""
        if self._template_cache is not None:
            return self._template_cache

        if not self.template_dir.exists():
            raise FileNotFoundError(
                f"Template directory not found: {self.template_dir}"
            )

        if not self.template_dir.is_dir():
            raise NotADirectoryError(
                f"Template path is not a directory: {self.template_dir}"
            )

        # Load shared macros if they exist
        macros_file = self.template_dir / "_macros.j2"
        macros = ""
        if macros_file.exists():
            macros = macros_file.read_text(encoding="utf-8") + "\n"

        # Load all template files (exclude partials starting with _)
        templates = []
        for template_file in sorted(self.template_dir.glob("*.j2")):
            if template_file.name.startswith(self.PARTIAL_PREFIX):
                continue

            content = template_file.read_text(encoding="utf-8")

            # Output filename: remove .j2 extension
            # e.g., "01_pvc_workload-pvc.yaml.j2" -> "01_pvc_workload-pvc.yaml"
            output_filename = template_file.stem
            if not output_filename.endswith(".yaml"):
                output_filename += ".yaml"

            templates.append(
                {
                    "filename": output_filename,
                    "content": macros + content,
                }
            )

        if not templates:
            raise ValueError(f"No template files found in: {self.template_dir}")

        self._template_cache = templates
        return templates

    def _render_template(self, template_content: str, values: dict) -> str:
        """Render a Jinja2 template string with the given values dict."""
        env = self._get_jinja_env()
        template = env.from_string(template_content)
        return template.render(**values)

    def _validate_yaml_files(self, directory: Path) -> list[str]:
        """Validate all YAML files in a directory, returning any error messages."""
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
        """Merge values, resolve overrides, render templates, and validate output for one stack."""
        if "name" not in stack:
            msg = f"Stack {stack_index} missing 'name' field, skipping"
            self.logger.log_warning(msg)
            result.global_errors.append(msg)
            return

        stack_name = stack["name"]
        self.logger.log_info(
            f"[{stack_index}/{total_stacks}] Processing stack: {stack_name}"
        )

        stack_errors = StackErrors()
        result.stacks[stack_name] = stack_errors

        stack_config = {k: v for k, v in stack.items() if k != "name"}
        merged_values = self.deep_merge(defaults, stack_config)

        if self.setup_overrides:
            merged_values = self.deep_merge(merged_values, self.setup_overrides)

        merged_values = self._apply_resource_preset(merged_values)

        self._log_image_overrides(merged_values)

        if self.version_resolver:
            try:
                merged_values = self.version_resolver.resolve_all(merged_values)
            except Exception as e:
                self.logger.log_warning(
                    f"Version resolution had issues for stack {stack_name}: {e}"
                )

        # Raises RuntimeError if "auto" values are present but cluster is unreachable
        if self.cluster_resource_resolver:
            merged_values = self.cluster_resource_resolver.resolve_all(
                merged_values
            )

        merged_values = self._resolve_namespace(merged_values)
        merged_values = self._resolve_model(merged_values)
        self._warn_custom_command_conflicts(merged_values)
        merged_values = self._resolve_deploy_method(merged_values)
        merged_values = self._resolve_monitoring(merged_values)
        merged_values = self._resolve_hf_token(merged_values)
        merged_values = self._resolve_model_id_label(merged_values)

        from llmdbenchmark.parser.config_schema import validate_config

        validation_warnings = validate_config(merged_values, self.logger)
        if validation_warnings:
            stack_errors.validation_warnings.extend(validation_warnings)
            self.logger.log_warning(
                f"Config validation found {len(validation_warnings)} issue(s) "
                f"for stack {stack_name}"
            )

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

        # Write resolved config (JSON round-trip strips YAML anchors)
        config_output = stack_output_dir / "config.yaml"
        try:
            resolved = json.loads(json.dumps(merged_values, default=str))
            with open(config_output, "w", encoding="utf-8") as f:
                yaml.dump(resolved, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            self.logger.log_warning(f"Failed to write config.yaml: {e}")

        yaml_errors = self._validate_yaml_files(stack_output_dir)
        if yaml_errors:
            self.logger.log_error("YAML validation issues:")
            for err in yaml_errors:
                self.logger.log_error(f"  {err}")
                stack_errors.yaml_errors.append(err)

        if not stack_errors.has_errors:
            result.rendered_paths.append(stack_output_dir)

        self.logger.log_info(f"Output: {stack_output_dir}")
        self.logger.log_info(f"Success: {success_count}, Errors: {error_count}")
        self.logger.line_break()

    def eval(self) -> RenderResult:
        """Run the full rendering pipeline and return a RenderResult."""
        result = RenderResult()

        try:
            defaults = self._load_yaml(self.defaults_file)
        except Exception as e:
            msg = f"Failed to load defaults file: {e}"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

        try:
            scenario = self._load_yaml(self.scenarios_file)
        except Exception as e:
            msg = f"Failed to load scenario file: {e}"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

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

        try:
            templates = self._load_templates()
        except Exception as e:
            msg = f"Failed to load templates: {e}"
            self.logger.log_error(msg)
            result.global_errors.append(msg)
            return result

        self.logger.log_info(
            f"Loaded {len(templates)} template(s) from {self.template_dir}"
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)

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
