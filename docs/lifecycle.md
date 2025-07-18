## Lifecycle

#### Standing up llm-d for experimentation and benchmarking

```
export LLMDBENCH_CLUSTER_URL="https://api.fmaas-platform-eval.fmaas.res.ibm.com"
export LLMDBENCH_CLUSTER_TOKEN="..."
```

> [!TIP]
> You can simply use your current context. **After running kubectl/oc login**, leaving `LLMDBENCH_CLUSTER_URL` undefined (or setting `export LLMDBENCH_CLUSTER_URL=auto`) will use your current context, with no need to configure `LLMDBENCH_CLUSTER_TOKEN`.

> [!IMPORTANT]
> No matter which method used (i.e., fully specify `LLMDBENCH_CLUSTER_URL` and `LLMDBENCH_CLUSTER_TOKEN` or simply use the current context), there is an additional variable which will always require definition: `LLMDBENCH_HF_TOKEN`

A complete list of available variables (and its default values) can be found by running
 `cat setup/env.sh | grep "^export LLMDBENCH_" | sort`

> [!NOTE]
> The `namespaces` specified by the environment variables `LLMDBENCH_VLLM_COMMON_NAMESPACE` and `LLMDBENCH_FMPERF_SERVICE_ACCOUNT` will be automatically created.

> [!TIP]
> If you want all generated `yaml` files and all data collected to reside on the same directory, set the environment variable `LLMDBENCH_CONTROL_WORK_DIR` explicitly before starting execution.

#### List of "standup steps"

Run the command line with the option `-h` in order to produce a list of steps

```
./setup/standup.sh -h
```

> [!NOTE]
> Each individual "step file" is named in a way that briefly describes each one the multiple steps required for a full deployment.

> [!TIP]
> Steps 0-5 can be considered "preparation" and can be skipped in most deployments.

#### to dry-run

```
./setup/standup.sh -n
```

### Deployment

vLLM instances can be deployed by one of the following methods:

- "standalone" (a simple deployment with services associated to the deployment)
- "modelservice" (invoking a combination of [llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra.git) and [llm-d-modelservice](https://github.com/llm-d/llm-d-model-service.git)).

This is controlled by the environment variable LLMDBENCH_DEPLOY_METHODS (default "modelservice"). The value of the environment variable can be overriden by the paraemeter `-t/--methods` (applicable for both `teardown.sh` and `standup.sh`)

> [!WARNING]
> At this time, only **one simultaneous** deployment method is supported

All available models are listed and controlled by the variable `LLMDBENCH_DEPLOY_MODEL_LIST`. The value of the above mentioned environment variable can be overriden by the paraemeter `-m/--model` (applicable for both `teardown.sh` and `standup.sh`).

> [!WARNING]
> At this time, only **one simultaneous** model is supported

### Scenarios

All relevant variables to a particular experiment are stored in a "scenario" (folder aptly named).

The expectation is that an experiment is run by initially executing:

```
source scenario/<scenario name>
```

### Full cycle (Standup/Run/Teardown)

At this point, with all the environment variables set (tip, `env | grep ^LLMDBENCH_ | sort`) you should be ready to deploy and test

```
./setup/standup.sh
```

> [!NOTE]
> The scenario can also be indicated as part of the command line optios for `standup.sh` (e.g. `./setup/standup.sh -c ocp_H100MIG_deployer_llama-3b`)

To re-execute only individual steps (full name or number):

```
./setup/standup.sh --step 08_smoketest.sh
./setup/standup.sh -s 7
./setup/standup.sh -s 3-5
./setup/standup.sh -s 5,7
```

Once llm-d is fully deployed, an experiment can be run. This script takes in different options where you can specify the harness, workload, etc. if they are not specified as a part of your scenario.

```
./run.sh
./run.sh --harness inference-perf --workload chatbot_synthetic.yaml
```

> [!IMPORTANT]
> This command will run an experiment, collect data and perform an initial analysis (generating statistics and plots). One can go straight to the analysis by adding the option `-z`/`--skip` to the above command

> [!NOTE]
> The scenario can also be indicated as part of the command line optios for `run.sh` (e.g., `./run.sh -c ocp_L40_standalone_llama-8b`)

Finally, cleanup everything

```
./setup/teardown.sh
```

> [!NOTE]
> The scenario can also be indicated as part of the command line optios for `teardown.sh` (e.g., `./teardown.sh -c kubernetes_H200_deployer_llama-8b`)

## Contribute

- [Instructions on how to contribute](CONTRIBUTING.md) including details on our development process and governance.
- We use Slack to discuss development across organizations. Please join: [Slack](https://inviter.co/llm-d-slack). There is a `sig-benchmarking` channel there.
- We host a weekly standup for contributors on Thursdays at 13:30 ET. Please join: [Meeting Details](https://calendar.google.com/calendar/u/0?cid=NzA4ZWNlZDY0NDBjYjBkYzA3NjdlZTNhZTk2NWQ2ZTc1Y2U5NTZlMzA5MzhmYTAyZmQ3ZmU1MDJjMDBhNTRiNEBncm91cC5jYWxlbmRhci5nb29nbGUuY29t). The meeting notes can be found [here](https://docs.google.com/document/d/1njjeyBJF6o69FlyadVbuXHxQRBGDLcIuT7JHJU3T_og/edit?usp=sharing). Joining the [llm-d google groups](https://groups.google.com/g/llm-d-contributors) will grant you access.

## License

This project is licensed under Apache License 2.0. See the [LICENSE file](LICENSE) for details.