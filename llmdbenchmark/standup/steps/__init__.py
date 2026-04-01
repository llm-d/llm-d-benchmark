"""Step registry for the standup phase.

Note: Smoketest and inference test steps have been moved to the
``llmdbenchmark.smoketests`` module and run as a separate phase
after standup (auto-chained by default, skippable with --skip-smoketest).
"""

from llmdbenchmark.executor.step import Step

from llmdbenchmark.standup.steps.step_00_ensure_infra import EnsureInfraStep
from llmdbenchmark.standup.steps.step_02_admin_prerequisites import (
    AdminPrerequisitesStep,
)
from llmdbenchmark.standup.steps.step_03_workload_monitoring import (
    WorkloadMonitoringStep,
)
from llmdbenchmark.standup.steps.step_04_model_namespace import ModelNamespaceStep
from llmdbenchmark.standup.steps.step_05_harness_namespace import HarnessNamespaceStep
from llmdbenchmark.standup.steps.step_06_standalone_deploy import StandaloneDeployStep
from llmdbenchmark.standup.steps.step_07_deploy_setup import DeploySetupStep
from llmdbenchmark.standup.steps.step_08_deploy_gaie import DeployGaieStep
from llmdbenchmark.standup.steps.step_09_deploy_modelservice import (
    DeployModelserviceStep,
)


def get_standup_steps() -> list[Step]:
    """Return all standup-phase steps in execution order."""
    return [
        EnsureInfraStep(),
        AdminPrerequisitesStep(),
        WorkloadMonitoringStep(),
        ModelNamespaceStep(),
        HarnessNamespaceStep(),
        StandaloneDeployStep(),
        DeploySetupStep(),
        DeployGaieStep(),
        DeployModelserviceStep(),
    ]
