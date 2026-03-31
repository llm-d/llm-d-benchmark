"""Step 04 -- Render workload profile templates with runtime values."""

import shutil
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.profile_renderer import (
    build_env_map,
    render_profile_file,
    apply_overrides,
)


class RenderProfilesStep(Step):
    """Render .yaml.in workload profiles with runtime values."""

    def __init__(self):
        super().__init__(
            number=5,
            name="render_profiles",
            description="Render workload profile templates",
            phase=Phase.RUN,
            per_stack=True,
        )

    def execute(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No stack path provided for per-stack step",
                errors=["stack_path is required"],
            )

        stack_name = stack_path.name
        errors: list[str] = []
        plan_config = self._load_stack_config(stack_path)

        # Resolve harness name
        harness_name = self._resolve(
            plan_config, "harness.name",
            context_value=context.harness_name, default="inference-perf",
        )

        # Resolve profile name
        profile_name = self._resolve(
            plan_config, "harness.experimentProfile", "harness.profile",
            context_value=context.harness_profile, default="sanity_random.yaml",
        )

        # Locate source profiles directory
        base_dir = context.base_dir or Path(__file__).resolve().parents[3]
        profiles_source = base_dir / "workload" / "profiles" / harness_name
        if not profiles_source.is_dir():
            errors.append(
                f"Profiles directory not found: {profiles_source}"
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Profile source directory not found",
                errors=errors,
                stack_name=stack_name,
            )

        # CLI flags and runtime values override plan_config defaults
        runtime_values: dict[str, str] = {
            "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL": (
                context.deployed_endpoints.get(stack_name, "")
            ),
        }

        if context.model_name:
            runtime_values["LLMDBENCH_DEPLOY_CURRENT_MODEL"] = context.model_name
            runtime_values["LLMDBENCH_DEPLOY_CURRENT_TOKENIZER"] = context.model_name

        if context.dataset_url:
            runtime_values["LLMDBENCH_RUN_DATASET_DIR"] = context.dataset_url

        env_map = build_env_map(
            plan_config=plan_config,
            runtime_values=runtime_values,
        )

        # Output directory for rendered profiles
        output_dir = context.workload_profiles_dir() / harness_name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy all profiles to output first (non-.yaml.in files are copied as-is)
        for src_file in profiles_source.iterdir():
            if src_file.is_file() and not src_file.name.endswith(".yaml.in"):
                shutil.copy2(src_file, output_dir / src_file.name)

        # Determine treatments
        treatments = self._resolve_treatments(context, plan_config)

        if not treatments:
            # Single default treatment -- render the profile as-is
            source_file = profiles_source / profile_name
            if not source_file.exists():
                # Try with .in extension
                source_file = profiles_source / f"{profile_name}.in"
            if source_file.exists():
                # Determine output name (strip .in if present)
                out_name = profile_name
                if out_name.endswith(".in"):
                    out_name = out_name[:-3]
                dest_file = output_dir / out_name

                if context.dry_run:
                    context.logger.log_info(
                        f"[DRY RUN] Would render {source_file.name} -> {dest_file}"
                    )
                else:
                    render_profile_file(source_file, dest_file, env_map)
                    context.logger.log_info(
                        f"Rendered profile: {dest_file.name}"
                    )
            else:
                errors.append(
                    f"Profile '{profile_name}' not found in {profiles_source}"
                )
        else:
            # Multiple treatments -- render one profile per treatment
            for i, treatment in enumerate(treatments):
                treatment_name = treatment.get("name", f"treatment-{i}")
                treatment_overrides = treatment.get("overrides", {})

                source_file = profiles_source / profile_name
                if not source_file.exists():
                    source_file = profiles_source / f"{profile_name}.in"

                if not source_file.exists():
                    errors.append(
                        f"Profile '{profile_name}' not found for treatment "
                        f"'{treatment_name}'"
                    )
                    continue

                out_name = profile_name
                if out_name.endswith(".in"):
                    out_name = out_name[:-3]
                # Append treatment name to output file
                stem = Path(out_name).stem
                suffix = Path(out_name).suffix
                dest_file = output_dir / f"{stem}-{treatment_name}{suffix}"

                if context.dry_run:
                    context.logger.log_info(
                        f"[DRY RUN] Would render treatment '{treatment_name}' "
                        f"-> {dest_file}"
                    )
                    continue

                render_profile_file(source_file, dest_file, env_map)

                # Apply treatment-specific overrides
                if treatment_overrides:
                    rendered_content = dest_file.read_text(encoding="utf-8")
                    overridden = apply_overrides(rendered_content, treatment_overrides)
                    dest_file.write_text(overridden, encoding="utf-8")

                context.logger.log_info(
                    f"Rendered profile: {dest_file.name} (treatment={treatment_name})"
                )

            # Store treatments in context for step 06
            context.experiment_treatments = treatments

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Some profiles could not be rendered",
                errors=errors,
                stack_name=stack_name,
            )

        context.logger.log_info(
            f"Profiles rendered to {output_dir}"
        )
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Profiles rendered for {stack_name}",
            stack_name=stack_name,
        )

    def _resolve_treatments(
        self, context: ExecutionContext, plan_config: dict | None
    ) -> list[dict]:
        """Parse treatments from --experiments file or --overrides flag.

        Returns [] for no treatments, or a list of {name, overrides} dicts.
        A top-level ``constants`` key in the experiments file is merged
        into every treatment's overrides before treatment-specific values.
        """
        treatments: list[dict] = []

        # --experiments file takes precedence
        if context.experiment_treatments_file:
            exp_path = Path(context.experiment_treatments_file)
            if exp_path.exists():
                with open(exp_path, encoding="utf-8") as f:
                    exp_data = yaml.safe_load(f)
                if isinstance(exp_data, dict):
                    constants: dict[str, str] = {}
                    raw_constants = exp_data.get("constants")
                    if isinstance(raw_constants, dict):
                        constants = {
                            k: str(v) for k, v in raw_constants.items()
                        }

                    # Look for 'treatments' or 'run' key
                    raw = exp_data.get("treatments") or exp_data.get("run", [])
                    if isinstance(raw, list):
                        for i, item in enumerate(raw):
                            if isinstance(item, dict):
                                # Constants first, then treatment overrides
                                overrides = dict(constants)
                                overrides.update({
                                    k: str(v)
                                    for k, v in item.items()
                                    if k != "name"
                                })
                                treatments.append({
                                    "name": item.get("name", f"t{i}"),
                                    "overrides": overrides,
                                })
                return treatments

        # --overrides creates a single treatment
        if context.profile_overrides:
            overrides: dict[str, str] = {}
            for pair in context.profile_overrides.split(","):
                pair = pair.strip()
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    overrides[key.strip()] = value.strip()
            if overrides:
                treatments.append({
                    "name": "override",
                    "overrides": overrides,
                })
            return treatments

        # No explicit treatments
        return []
