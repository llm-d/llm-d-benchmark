"""Step 04 -- Render workload profile templates with runtime values."""

import posixpath
import shutil
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.experiment.parser import (
    MAX_PARALLEL_TREATMENTS_CAP,
    TreatmentGroup,
    groups_from_treatments,
    read_treatment_groups,
)
from llmdbenchmark.experiment.bands import (
    BAND_GROUP_NAME,
    OBJECTIVE_HEADER_KEY,
    build_band_treatments,
    extract_flow_control,
    find_endpoint_picker_config,
    list_inference_objectives,
    load_per_band_overrides,
    pair_bands_with_objectives,
)
from llmdbenchmark.parser.cli_overrides import (
    coerce_value,
    split_override_pairs,
    surprising_coercion,
)
from llmdbenchmark.utilities.profile_renderer import (
    build_env_map,
    render_profile_file,
    apply_overrides,
    classify_override_miss,
)


def _parse_cli_overrides(context: ExecutionContext) -> dict[str, Any]:
    """Parse the ``-o/--overrides`` flag into flat dotted-key overrides.

    Shared by the plain single-treatment path and the per-band path, so
    ``-o`` shapes every band instance the same way. Values are parsed as
    YAML, same as ``--set``.
    """
    overrides: dict[str, Any] = {}
    if not context.profile_overrides:
        return overrides
    for pair in split_override_pairs(context.profile_overrides):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        coerced = coerce_value(value)
        warning = surprising_coercion(value, coerced)
        if warning:
            context.logger.log_warning(f"-o {key.strip()}: {warning}")
        overrides[key.strip()] = coerced
    return overrides


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
            plan_config,
            "harness.name",
            context_value=context.harness_name,
            default="inference-perf",
        )

        # Resolve profile name
        profile_name = self._resolve(
            plan_config,
            "harness.experimentProfile",
            "harness.profile",
            context_value=context.harness_profile,
            default="sanity_random.yaml",
        )

        # Locate source profiles directory
        base_dir = context.base_dir or Path(__file__).resolve().parents[3]
        workload_file_path = getattr(context, "workload_file_path", None)
        source_profile_file: Path | None = None
        profiles_source: Path | None = None
        if workload_file_path:
            source_profile_file = Path(workload_file_path).expanduser()
            if not source_profile_file.is_absolute():
                source_profile_file = Path.cwd() / source_profile_file
            if not source_profile_file.is_file():
                errors.append(f"Workload profile file not found: {source_profile_file}")
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message="Workload profile file not found",
                    errors=errors,
                    stack_name=stack_name,
                )
            profile_name = source_profile_file.name
        else:
            profiles_source = base_dir / "workload" / "profiles" / harness_name
            if not profiles_source.is_dir():
                exp_profiles = base_dir / "experimental" / harness_name / "profiles"
                if exp_profiles.is_dir():
                    profiles_source = exp_profiles
            if not profiles_source.is_dir():
                errors.append(f"Profiles directory not found: {profiles_source}")
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

        dataset_file_override: str | None = None
        if context.dataset_url:
            # For s3:// URLs the harness shell script downloads the file into
            # /requests/datasets/ at pod runtime, so DIR must point at the
            # local landing path, not at the s3:// prefix. For local-style
            # URLs we still treat them as filesystem paths.
            #
            # Trailing slash => directory replay: DIR is the (resolved) path,
            # FILE is empty.
            # Otherwise (path ends with a filename/extension) => single-file
            # replay: DIR is the parent (or the local landing dir for s3://),
            # FILE is the basename.
            is_s3 = context.dataset_url.startswith("s3://")
            s3_local_dir = "/requests/datasets"
            if context.dataset_url.endswith("/"):
                if is_s3:
                    runtime_values["LLMDBENCH_RUN_DATASET_DIR"] = s3_local_dir
                else:
                    runtime_values["LLMDBENCH_RUN_DATASET_DIR"] = context.dataset_url
                dataset_file_override = ""
            else:
                dir_part, file_part = posixpath.split(context.dataset_url)
                if is_s3:
                    runtime_values["LLMDBENCH_RUN_DATASET_DIR"] = s3_local_dir
                else:
                    runtime_values["LLMDBENCH_RUN_DATASET_DIR"] = dir_part
                runtime_values["LLMDBENCH_RUN_DATASET_FILE"] = file_part

        env_map = build_env_map(
            plan_config=plan_config,
            runtime_values=runtime_values,
        )
        if dataset_file_override is not None:
            env_map["LLMDBENCH_RUN_DATASET_FILE"] = dataset_file_override

        if getattr(context, "harness_debug", False) and workload_file_path is None:
            return self._render_all_debug_profiles(
                context,
                base_dir,
                env_map,
                stack_name,
            )

        # Output directory for rendered profiles
        output_dir = context.workload_profiles_dir() / harness_name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy all profiles to output first (non-.yaml.in files are copied as-is)
        if profiles_source is not None:
            for src_file in profiles_source.iterdir():
                if src_file.is_file() and not src_file.name.endswith(".yaml.in"):
                    shutil.copy2(src_file, output_dir / src_file.name)

        # Determine treatments. Band discovery fails as a step error (not
        # an exception) so the message reaches the run report.
        try:
            treatments = self._resolve_treatments(context, plan_config)
        except ValueError as exc:
            errors.append(str(exc))
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Treatments could not be resolved",
                errors=errors,
                stack_name=stack_name,
            )

        if not treatments:
            # Single default treatment -- render the profile as-is
            source_file = self._resolve_source_file(
                profile_name, profiles_source, source_profile_file
            )
            if source_file is not None:
                # Determine output name (strip .in if present)
                out_name = profile_name
                if out_name.endswith(".in"):
                    out_name = out_name[:-3]
                dest_file = output_dir / out_name
                if source_profile_file is not None:
                    context.harness_profile = out_name

                if context.dry_run:
                    context.logger.log_info(
                        f"[DRY RUN] Would render {source_file.name} -> {dest_file}"
                    )
                else:
                    render_profile_file(source_file, dest_file, env_map)
                    context.logger.log_info(f"Rendered profile: {dest_file.name}")
            else:
                errors.append(
                    f"Profile '{profile_name}' not found in {profiles_source}"
                )
        else:
            # Multiple treatments -- render one profile per treatment
            for i, treatment in enumerate(treatments):
                treatment_name = treatment.get("name", f"treatment-{i}")
                treatment_overrides = treatment.get("overrides", {})

                treatment_profile = treatment.get("profile") or profile_name
                source_file = self._resolve_source_file(
                    treatment_profile, profiles_source, source_profile_file
                )
                if source_file is None:
                    errors.append(
                        f"Profile '{treatment_profile}' not found for treatment "
                        f"'{treatment_name}'"
                    )
                    continue

                out_name = treatment_profile
                if out_name.endswith(".in"):
                    out_name = out_name[:-3]
                if source_profile_file is not None:
                    context.harness_profile = out_name
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

                # Apply treatment-specific overrides. Band-generated
                # treatments may grow the profile (a base profile without
                # an api.headers block), and their objective header must
                # land or the instance would silently target the default
                # band -- so a miss there is a hard error, not a warning.
                if treatment_overrides:
                    rendered_content = dest_file.read_text(encoding="utf-8")
                    overridden, unmatched = apply_overrides(
                        rendered_content,
                        treatment_overrides,
                        create_missing_parents=bool(treatment.get("band")),
                    )
                    dest_file.write_text(overridden, encoding="utf-8")
                    if treatment.get("band") and OBJECTIVE_HEADER_KEY in unmatched:
                        errors.append(
                            f"Treatment '{treatment_name}': the objective "
                            f"header override '{OBJECTIVE_HEADER_KEY}' did "
                            f"not land in the profile; refusing to run an "
                            f"instance that cannot target its band."
                        )
                    # Surface silent no-ops: a treatment override whose parent
                    # path doesn't exist in the workload profile (most often a
                    # plan-level field like decode.replicas or
                    # router.epp.pluginsConfigFile placed under the
                    # top-level treatments: block instead of setup.treatments).
                    for missed_key in unmatched:
                        context.logger.log_warning(
                            f"Treatment '{treatment_name}': "
                            + classify_override_miss(missed_key)
                        )

                context.logger.log_info(
                    f"Rendered profile: {dest_file.name} (treatment={treatment_name})"
                )

            # Store treatments in context for step 06
            context.experiment_treatments = treatments
            if not getattr(context, "treatment_groups", None):
                context.treatment_groups = groups_from_treatments(treatments)

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Some profiles could not be rendered",
                errors=errors,
                stack_name=stack_name,
            )

        context.logger.log_info(f"Profiles rendered to {output_dir}")
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Profiles rendered for {stack_name}",
            stack_name=stack_name,
        )

    def _render_all_debug_profiles(
        self,
        context: ExecutionContext,
        base_dir: Path,
        env_map: dict[str, str],
        stack_name: str,
    ) -> StepResult:
        """Render every built-in harness profile for a debug harness pod."""
        profiles_root = base_dir / "workload" / "profiles"
        if not profiles_root.is_dir():
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Profile source directory not found",
                errors=[f"Profiles directory not found: {profiles_root}"],
                stack_name=stack_name,
            )

        rendered_count = 0
        harness_count = 0
        for harness_dir in sorted(profiles_root.iterdir()):
            if not harness_dir.is_dir():
                continue

            output_dir = context.workload_profiles_dir() / harness_dir.name
            output_dir.mkdir(parents=True, exist_ok=True)
            harness_count += 1

            for src_file in sorted(harness_dir.iterdir()):
                if not src_file.is_file():
                    continue
                dest_name = (
                    src_file.name[:-3]
                    if src_file.name.endswith(".in")
                    else src_file.name
                )
                dest_file = output_dir / dest_name
                if context.dry_run:
                    context.logger.log_info(
                        f"[DRY RUN] Would render debug profile "
                        f"{harness_dir.name}/{src_file.name} -> {dest_file}"
                    )
                    rendered_count += 1
                    continue
                if src_file.name.endswith(".yaml.in"):
                    render_profile_file(src_file, dest_file, env_map)
                else:
                    shutil.copy2(src_file, dest_file)
                rendered_count += 1

        context.logger.log_info(
            f"Debug profiles rendered to {context.workload_profiles_dir()}"
        )
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Rendered {rendered_count} debug workload profile(s) "
                f"across {harness_count} harness(es)"
            ),
            stack_name=stack_name,
        )

    def _resolve_source_file(
        self,
        profile_name: str,
        profiles_source: Path | None,
        source_profile_file: Path | None,
    ) -> Path | None:
        if source_profile_file is not None:
            return source_profile_file

        if profiles_source is None:
            return None

        source_file = profiles_source / profile_name
        if source_file.exists():
            return source_file

        source_file = profiles_source / f"{profile_name}.in"
        if source_file.exists():
            return source_file

        return None

    def _resolve_treatments(
        self, context: ExecutionContext, plan_config: dict | None
    ) -> list[dict]:
        """Parse treatments from --experiments file or --overrides flag.

        Returns [] for no treatments, or a list of {name, overrides} dicts.
        A top-level ``constants`` key in the experiments file is merged
        into every treatment's overrides before treatment-specific values.

        A top-level ``groups`` block is flattened here, in group order, so every
        downstream step keeps consuming one flat treatment list; each treatment
        carries its ``group`` name for the pod label and the benchmark report.
        """
        treatments: list[dict] = []

        # --per-band-traffic generates its own treatments from the EPP
        # flow-control bands; it cannot be combined with the sources below
        # (cli.py rejects an experiments file with run treatments already).
        if getattr(context, "per_band_traffic", False):
            return self._resolve_band_treatments(context, plan_config)

        # --experiments file takes precedence
        if context.experiment_treatments_file:
            groups = read_treatment_groups(context.experiment_treatments_file)
            if groups:
                context.treatment_groups = groups
                return [t for group in groups for t in group.treatments]

            exp_path = Path(context.experiment_treatments_file)
            if exp_path.exists():
                with open(exp_path, encoding="utf-8") as f:
                    exp_data = yaml.safe_load(f)
                if isinstance(exp_data, dict):
                    constants: dict[str, Any] = {}
                    raw_constants = exp_data.get("constants")
                    if isinstance(raw_constants, dict):
                        constants = {str(k): v for k, v in raw_constants.items()}

                    # Look for 'treatments' or 'run' key
                    raw = exp_data.get("treatments") or exp_data.get("run", [])
                    if isinstance(raw, list):
                        for i, item in enumerate(raw):
                            if isinstance(item, dict):
                                # Constants first, then treatment overrides
                                overrides = dict(constants)
                                overrides.update(
                                    {
                                        str(k): v
                                        for k, v in item.items()
                                        if k not in {"name", "profile"}
                                    }
                                )
                                treatment = {
                                    "name": item.get("name", f"t{i}"),
                                    "overrides": overrides,
                                }
                                if item.get("profile"):
                                    treatment["profile"] = str(item["profile"])
                                treatments.append(treatment)
                return treatments

        # --overrides creates a single treatment. Values are parsed as YAML,
        # same as --set, so a map or list value works too.
        if context.profile_overrides:
            overrides = _parse_cli_overrides(context)
            if overrides:
                treatments.append(
                    {
                        "name": "override",
                        "overrides": overrides,
                    }
                )
            return treatments

        # No explicit treatments
        return []

    def _resolve_band_treatments(
        self, context: ExecutionContext, plan_config: dict | None
    ) -> list[dict]:
        """Build one workload treatment per EPP flow-control band.

        Bands come from the rendered stack's EPP plugins config; the
        targetable class names come from the InferenceObjectives on the
        cluster. Both layers meet in
        :mod:`llmdbenchmark.experiment.bands`; this method wires the result
        into the regular treatment machinery (one group, concurrent pods,
        ``max_parallel_treatments`` raised to the instance count unless the
        operator pinned it).
        """
        picker = find_endpoint_picker_config(plan_config)
        bands = extract_flow_control(picker) if picker is not None else None
        if picker is not None and bands is None:
            raise ValueError(
                "--per-band-traffic: flow control is not enabled in this "
                "stack's EPP configuration (no flowControl feature gate or "
                "no priorityBands). Per-band traffic needs flow control."
            )
        if picker is None:
            context.logger.log_warning(
                "--per-band-traffic: no EPP plugins config found in the "
                "rendered stack (run-only mode?); discovering objectives "
                "without cross-checking bands."
            )

        namespace = context.namespace or (plan_config or {}).get("namespace", {}).get(
            "name"
        )
        objectives = list_inference_objectives(context.cmd, namespace)
        instances, warnings = pair_bands_with_objectives(bands, objectives)
        for warning in warnings:
            context.logger.log_warning(f"--per-band-traffic: {warning}")

        try:
            per_band = load_per_band_overrides(
                getattr(context, "per_band_overrides_file", None)
            )
        except ValueError as exc:
            raise ValueError(f"--per-band-traffic: {exc}") from exc

        treatments, errors = build_band_treatments(
            instances, _parse_cli_overrides(context), per_band
        )
        if errors:
            raise ValueError("--per-band-traffic: " + "; ".join(errors))
        if not treatments:
            raise ValueError(
                "--per-band-traffic: no band instances were discovered. "
                "Define flow-control priorityBands in the EPP config and "
                "InferenceObjective CRs whose spec.priority selects them "
                "(the x-llm-d-inference-objective header resolves through "
                "those CRs)."
            )

        context.treatment_groups = [
            TreatmentGroup(name=BAND_GROUP_NAME, treatments=treatments)
        ]
        if (
            not context.max_parallel_treatments_explicit
            and context.max_parallel_treatments < len(treatments)
        ):
            context.max_parallel_treatments = min(
                MAX_PARALLEL_TREATMENTS_CAP, len(treatments)
            )
            context.logger.log_info(
                f"max_parallel_treatments raised to "
                f"{context.max_parallel_treatments} for the "
                f"{len(treatments)} band instance(s)"
            )
        if len(treatments) > MAX_PARALLEL_TREATMENTS_CAP:
            raise ValueError(
                f"--per-band-traffic: discovered {len(treatments)} band "
                f"instances but the concurrency cap is "
                f"{MAX_PARALLEL_TREATMENTS_CAP}; the instances would "
                f"serialize and stop being concurrent. Reduce the number "
                f"of bands/objectives or raise the cap."
            )

        summary = ", ".join(
            f"{instance.treatment_name} (priority {instance.band_priority})"
            for instance in instances
        )
        context.logger.log_info(f"Per-band traffic instances: {summary}")
        return treatments
