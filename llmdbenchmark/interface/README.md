# llmdbenchmark.interface

CLI subcommand definitions and environment variable helpers. Each subcommand module registers its arguments with argparse, using environment variables as defaults where applicable.

## Files

```
interface/
├── __init__.py      -- Empty package marker
├── commands.py      -- Command enum
├── env.py           -- Environment variable helpers
├── plan.py          -- plan subcommand
├── standup.py       -- standup subcommand
├── smoketest.py     -- smoketest subcommand
├── run.py           -- run subcommand
├── teardown.py      -- teardown subcommand
└── experiment.py    -- experiment subcommand
```

## Command Enum (`commands.py`)

```python
class Command(Enum):
    PLAN = "plan"
    STANDUP = "standup"
    SMOKETEST = "smoketest"
    RUN = "run"
    TEARDOWN = "teardown"
    EXPERIMENT = "experiment"
```

Each subcommand module provides an `add_subcommands(parser)` function that registers the subcommand and its arguments with `argparse._SubParsersAction`. The main `cli.py` iterates over the interface modules, calls `add_subcommands()` for each, and dispatches to the appropriate phase executor based on the selected command.

## Environment Variable Helpers (`env.py`)

```python
def env(name: str, default=None):
    """Return env var value or default. For use as argparse default=."""

def env_bool(name: str, default: bool = False) -> bool:
    """Return env var as boolean. Truthy: '1', 'true', 'yes' (case-insensitive)."""

def env_int(name: str, default: int | None = None) -> int | None:
    """Return env var as int, or default if not set / not parseable."""
```

## Subcommand Definitions

### plan (`plan.py`)

Generates deployment plans (YAML/Helm manifests) without executing anything on the cluster. Accepts rendering-related flags so the output matches what standup would produce.

| Flag | Env Var | Description |
|------|---------|-------------|
| `-p` / `--namespace` | `LLMDBENCH_NAMESPACE` | Namespace(s) for rendering |
| `-m` / `--models` | `LLMDBENCH_MODELS` | Model to render the plan for |
| `-t` / `--methods` | `LLMDBENCH_METHODS` | Deploy method (standalone, modelservice, fma) |
| `-f` / `--monitoring` | -- | Enable monitoring in rendered templates |
| `-k` / `--kubeconfig` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig for cluster resource auto-detection |

### standup (`standup.py`)

Provisions model infrastructure from a specification. Implicitly generates a plan, then executes standup steps.

| Flag | Env Var | Description |
|------|---------|-------------|
| `-s` / `--step` | -- | Step list (e.g. `0,1,5` or `1-7`) |
| `-c` / `--scenario` | `LLMDBENCH_SCENARIO` | Scenario file |
| `-m` / `--models` | `LLMDBENCH_MODELS` | Models to stand up |
| `-p` / `--namespace` | `LLMDBENCH_NAMESPACE` | Namespace(s) |
| `-t` / `--methods` | `LLMDBENCH_METHODS` | Deploy method (standalone, modelservice, fma) |
| `-a` / `--affinity` | `LLMDBENCH_AFFINITY` | Node affinity config |
| `-b` / `--annotations` | `LLMDBENCH_ANNOTATIONS` | Pod annotations |
| `-r` / `--release` | `LLMDBENCH_RELEASE` | Helm chart release name |
| `-u` / `--wva` | `LLMDBENCH_WVA` | Enable Workload Variant Autoscaler |
| `-f` / `--monitoring` | -- | Enable PodMonitor and metrics scraping |
| `--parallel` | `LLMDBENCH_PARALLEL` | Max parallel stacks (default: 4) |
| `-k` / `--kubeconfig` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |
| `--skip-smoketest` | -- | Skip automatic post-standup smoketests |
| `--standalone-deploy-timeout` | `LLMDBENCH_STANDALONE_DEPLOY_TIMEOUT` | Seconds to wait for the vLLM pods to deploy during standup in standalone mode. |
| `--gateway-deploy-timeout` | `LLMDBENCH_GATEWAY_DEPLOY_TIMEOUT` | Seconds to wait for gateway infrastructure pods to deploy during standup with modelservice. |
| `--modelservice-deploy-timeout` | `LLMDBENCH_MODELSERVICE_DEPLOY_TIMEOUT` | Seconds to wait for decode, prefill and inference pool pods to deploy during standup with modelservice (Generic timeout for Step 9). |

### smoketest (`smoketest.py`)

Validates a deployed model infrastructure with health checks, inference test, and config validation.

| Flag | Env Var | Description |
|------|---------|-------------|
| `-s` / `--step` | -- | Step list |
| `-p` / `--namespace` | `LLMDBENCH_NAMESPACE` | Namespace(s) |
| `-t` / `--methods` | `LLMDBENCH_METHODS` | Deploy methods |
| `--parallel` | `LLMDBENCH_PARALLEL` | Max parallel stacks (default: 4) |
| `-k` / `--kubeconfig` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |

### run (`run.py`)

Executes benchmark experiments against deployed infrastructure.

| Flag | Env Var | Description |
|------|---------|-------------|
| `-s` / `--step` | -- | Step list |
| `-p` / `--namespace` | `LLMDBENCH_NAMESPACE` | Namespace(s) |
| `-t` / `--methods` | `LLMDBENCH_METHODS` | Deploy method |
| `-k` / `--kubeconfig` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |
| `-m` / `--model` | `LLMDBENCH_MODEL` | Model name override |
| `-l` / `--harness` | `LLMDBENCH_HARNESS` | Harness name |
| `-w` / `--workload` | `LLMDBENCH_WORKLOAD` | Workload profile name |
| `-e` / `--experiments` | `LLMDBENCH_EXPERIMENTS` | Experiment treatments YAML |
| `-o` / `--overrides` | `LLMDBENCH_OVERRIDES` | Profile parameter overrides (param=value,...) |
| `-r` / `--output` | `LLMDBENCH_OUTPUT` | Results destination (local, gs://, s3://) |
| `-j` / `--parallelism` | `LLMDBENCH_PARALLELISM` | Parallel harness pods |
| `--wait-timeout` | `LLMDBENCH_WAIT_TIMEOUT` | Wait timeout in seconds (0 = don't wait) |
| `-x` / `--dataset` | `LLMDBENCH_DATASET` | Dataset URL for replay |
| `-f` / `--monitoring` | -- | Enable metrics scraping and log capture |
| `-q` / `--serviceaccount` | `LLMDBENCH_SERVICE_ACCOUNT` | Service account for harness pods |
| `-g` / `--envvarspod` | `LLMDBENCH_HARNESS_ENVVARS_TO_YAML` | Env vars to propagate to harness pods |
| `-z` / `--skip` | -- | Skip execution, collect existing results only |
| `-d` / `--debug` | -- | Start harness pods with `sleep infinity` |
| `--analyze` | `LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY` | Run local analysis on results |
| `-U` / `--endpoint-url` | `LLMDBENCH_ENDPOINT_URL` | Explicit endpoint URL (run-only mode) |
| `-c` / `--config` | -- | Run config YAML file (run-only mode) |
| `--generate-config` | -- | Generate run config and exit |
| `--data-access-timeout` | `LLMDBENCH_DATA_ACCESS_TIMEOUT` | Seconds to wait for the harness data-access pod to become Ready. |

### teardown (`teardown.py`)

Removes resources deployed by a previous standup.

| Flag | Env Var | Description |
|------|---------|-------------|
| `-s` / `--step` | -- | Step list |
| `-m` / `--models` | `LLMDBENCH_MODELS` | Model that was deployed |
| `-t` / `--methods` | `LLMDBENCH_METHODS` | Deploy methods to tear down |
| `-r` / `--release` | `LLMDBENCH_RELEASE` | Helm chart release name (default: llmdbench) |
| `-d` / `--deep` | -- | Deep clean: delete ALL resources in namespaces |
| `-p` / `--namespace` | `LLMDBENCH_NAMESPACE` | Namespace(s) to tear down |
| `-k` / `--kubeconfig` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |

### experiment (`experiment.py`)

Orchestrates a full DoE experiment with automatic standup/run/teardown per setup treatment.

| Flag | Env Var | Description |
|------|---------|-------------|
| `-e` / `--experiments` | `LLMDBENCH_EXPERIMENTS` | Experiment YAML file (required) |
| `-p` / `--namespace` | `LLMDBENCH_NAMESPACE` | Namespace(s) |
| `-t` / `--methods` | `LLMDBENCH_METHODS` | Deploy method |
| `-m` / `--models` | `LLMDBENCH_MODELS` | Models to deploy |
| `-k` / `--kubeconfig` | `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` | Kubeconfig path |
| `--parallel` | `LLMDBENCH_PARALLEL` | Max parallel stacks (default: 4) |
| `-f` / `--monitoring` | -- | Enable PodMonitor and metrics scraping |
| `-l` / `--harness` | `LLMDBENCH_HARNESS` | Harness name |
| `-w` / `--workload` | `LLMDBENCH_WORKLOAD` | Workload profile name |
| `-o` / `--overrides` | `LLMDBENCH_OVERRIDES` | Profile overrides |
| `-r` / `--output` | `LLMDBENCH_OUTPUT` | Results destination |
| `-j` / `--parallelism` | `LLMDBENCH_PARALLELISM` | Parallel harness pods per treatment |
| `--wait-timeout` | `LLMDBENCH_WAIT_TIMEOUT` | Wait timeout in seconds |
| `-x` / `--dataset` | `LLMDBENCH_DATASET` | Dataset URL |
| `-d` / `--debug` | -- | Debug mode (sleep infinity) |
| `--stop-on-error` | -- | Abort experiment on first failure |
| `--skip-teardown` | -- | Leave stacks running after each treatment |
| `--data-access-timeout` | `LLMDBENCH_DATA_ACCESS_TIMEOUT` | Seconds to wait for the harness data-access pod to become Ready. |
