# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Development Commands

### Build & Test
```bash
# Run tests
make test

# Run linting
make lint

# Format Go code
make format

# Build container image
make image-build

# Push container image
make image-push
```

### Benchmark Workflow Commands

**End-to-End Benchmarking (standup + run + teardown):**
```bash
# Basic e2e benchmark with defaults (llm-d modelservice, Llama-3.2-1B-Instruct)
./e2e.sh

# Custom scenario with specific model
./e2e.sh -c scenarios/my-scenario.sh -m meta-llama/Meta-Llama-3-8B-Instruct
```

**Individual Steps:**
```bash
# Stand up an llm-d stack
./setup/standup.sh -c scenarios/my-scenario.sh -m meta-llama/Meta-Llama-3-8B-Instruct

# Run benchmark against existing stack
./run.sh -t <service-or-pod-name> -l inference-perf -w sanity_random

# Tear down the stack
./setup/teardown.sh -c scenarios/my-scenario.sh
```

**Running Benchmarks Against Existing Stacks:**
```bash
# Find a matching service/pod in namespace
./run.sh -t my-vllm-service -p my-namespace

# Run with custom workload profile
./run.sh -t my-service -w chatbot_synthetic -o "rate=5,duration=60"

# Debug mode (creates pod with sleep infinity)
./run.sh -t my-service -d
```

### Key Command Line Options

- `-c/--scenario`: Scenario file with environment variable overrides
- `-m/--models`: Comma-separated list of models to deploy/test
- `-p/--namespace`: Target namespace (deployment,harness)
- `-t/--methods`: Deployment methods (standalone, modelservice, or service name)
- `-l/--harness`: Load generator (inference-perf, guidellm, vllm-benchmark, nop)
- `-w/--workload`: Workload profile to use
- `-o/--overrides`: Key=value pairs to override workload parameters
- `-j/--parallelism`: Number of parallel harness pods
- `-n/--dry-run`: Print commands without executing
- `-v/--verbose`: Show detailed command execution

## High-Level Architecture

### Core Components

1. **Setup Framework (`setup/`)**
   - `standup.sh`: Deploys llm-d stacks (standalone vLLM or llm-d modelservice)
   - `run.sh`: Executes benchmarks against deployed stacks
   - `teardown.sh`: Cleans up deployed resources
   - `e2e.sh`: Orchestrates full benchmark experiments (standup → run → teardown)
   - `env.sh`: Central environment configuration and functions

2. **Deployment Methods**
   - **Standalone**: Direct vLLM pods with deployment + service
   - **ModelService**: Full llm-d stack using llm-d-infra + llm-d-modelservice Helm charts

3. **Harness System (`workload/`)**
   - Multiple load generators supported via pluggable architecture
   - Harness pods created in target cluster with associated PVCs for results
   - Results collected back to workstation for analysis

4. **Scenario Management (`scenarios/`)**
   - Environment files containing deployment parameters
   - Override defaults for GPU types, model configs, vLLM settings
   - Well-Lit Path guides available for specific configurations

5. **Experiment Orchestration**
   - Design of Experiments (DOE) support for parameter sweeps
   - Treatment generation for both standup and run parameters
   - Automatic result collection and analysis

### Key Design Patterns

1. **Environment Variable Configuration**
   - All parameters exposed as `LLMDBENCH_*` environment variables
   - CLI options override environment variables via `LLMDBENCH_CLIOVERRIDE_*`
   - Scenario files provide collections of related overrides

2. **Template Rendering**
   - Workload profiles use `.yaml.in` templates
   - `REPLACE_ENV_*` placeholders substituted at runtime
   - Supports dynamic parameter injection

3. **Multi-Stage Execution**
   - Numbered steps in `setup/steps/` for standup process
   - Python scripts handle complex operations (capacity planning, pod creation)
   - Shell scripts coordinate workflow

4. **Result Management**
   - Results stored in PVCs during execution
   - Local analysis via conda environments
   - Benchmark reports generated in standardized format

### Integration Points

- **Kubernetes**: Primary deployment target
- **OpenShift**: Special handling for routes and security contexts
- **Helm**: Used for llm-d modelservice deployments
- **External Dependencies**:
  - llm-d-infra: Infrastructure components
  - llm-d-modelservice: Model serving charts
  - inference-perf, guidellm, vllm: Load generators

### Configuration Explorer (`config_explorer/`)

Separate component for capacity planning and configuration optimization:
- Validates vLLM configurations before deployment
- Recommends optimal GPU and parameter configurations
- Provides cost analysis for different deployment options

## Important Environment Variables

Key variables that control benchmark behavior:
- `LLMDBENCH_DEPLOY_SCENARIO`: Scenario file to use
- `LLMDBENCH_DEPLOY_METHODS`: Deployment method (standalone/modelservice)
- `LLMDBENCH_VLLM_COMMON_NAMESPACE`: Target namespace for deployment
- `LLMDBENCH_HARNESS_NAME`: Load generator to use
- `LLMDBENCH_HARNESS_EXPERIMENT_PROFILE`: Workload profile
- `LLMDBENCH_CONTROL_WORK_DIR`: Working directory for results

## Directory Structure

- `setup/`: Core scripts and deployment logic
- `scenarios/`: Deployment scenario configurations
- `workload/`: Harness implementations and profiles
- `analysis/`: Result analysis scripts
- `config_explorer/`: Configuration optimization tools
- `docs/`: Detailed documentation
- `experiments/`: DOE experiment definitions
- `build/`: Container build files