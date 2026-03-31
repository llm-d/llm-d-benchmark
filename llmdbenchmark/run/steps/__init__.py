"""Step registry for the run phase."""

from llmdbenchmark.executor.step import Step

from llmdbenchmark.run.steps.step_00_preflight import RunPreflightStep
from llmdbenchmark.run.steps.step_01_cleanup_previous import RunCleanupPreviousStep
from llmdbenchmark.run.steps.step_02_harness_namespace import HarnessNamespaceStep
from llmdbenchmark.run.steps.step_03_detect_endpoint import DetectEndpointStep
from llmdbenchmark.run.steps.step_04_verify_model import VerifyModelStep
from llmdbenchmark.run.steps.step_05_render_profiles import RenderProfilesStep
from llmdbenchmark.run.steps.step_06_create_profile_configmap import (
    CreateProfileConfigmapStep,
)
from llmdbenchmark.run.steps.step_07_deploy_harness import DeployHarnessStep
from llmdbenchmark.run.steps.step_08_wait_completion import WaitCompletionStep
from llmdbenchmark.run.steps.step_09_collect_results import CollectResultsStep
from llmdbenchmark.run.steps.step_10_upload_results import UploadResultsStep
from llmdbenchmark.run.steps.step_11_cleanup_post import RunCleanupPostStep
from llmdbenchmark.run.steps.step_12_analyze_results import AnalyzeResultsStep
from llmdbenchmark.executor.step import Phase


def get_run_steps() -> list[Step]:
    """Return all run-phase steps in execution order."""

    return [
        RunPreflightStep(),
        RunCleanupPreviousStep(),
        HarnessNamespaceStep(),
        DetectEndpointStep(),
        VerifyModelStep(),
        RenderProfilesStep(),
        CreateProfileConfigmapStep(),
        DeployHarnessStep(),
        WaitCompletionStep(),
        CollectResultsStep(),
        AnalyzeResultsStep(),
        UploadResultsStep(),
        RunCleanupPostStep(),
    ]
