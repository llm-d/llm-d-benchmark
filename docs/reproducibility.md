## Reproducibility

All the information collected inside the directory pointed by the environment variable `LLMDBENCH_CONTROL_WORK_DIR` should be enough to allow others to reproduce the experiment with the same parameters. In particular, all the parameters - always exposed as environment variables - applied to `llm-d` or `vllm` stacks can be found at `${LLMDBENCH_CONTROL_WORK_DIR}/environment/variables`

A sample output of the content of `${LLMDBENCH_CONTROL_WORK_DIR}` for a very simple experiment is shown here

```
./analysis
./analysis/data
./analysis/data/stats.txt
./analysis/plots
./analysis/plots/latency_analysis.png
./analysis/plots/README.md
./analysis/plots/throughput_analysis.png
./setup
./setup/yamls
./setup/yamls/05_pvc_workload-pvc.yaml
./setup/yamls/pod_benchmark-launcher.yaml
./setup/yamls/05_b_service_access_to_fmperf_data.yaml
./setup/yamls/07_deployer_values.yaml
./setup/yamls/05_namespace_sa_rbac_secret.yaml
./setup/yamls/04_prepare_namespace_llama-3b.yaml
./setup/yamls/05_a_pod_access_to_fmperf_data.yaml
./setup/yamls/03_cluster-monitoring-config_configmap.yaml
./setup/commands
./setup/commands/1748350741979704000_command.log
...
./setup/commands/1748350166902915000_command.log
./setup/sed-commands
./results
./results/LMBench_short_input_qps0.5.csv
./results/pod_log_response.txt
./environment
./environment/context.ctx
./environment/variables
./workload
./workload/harnesses
./workload/profiles
./workload/profiles/sanity_short-input.yaml
```