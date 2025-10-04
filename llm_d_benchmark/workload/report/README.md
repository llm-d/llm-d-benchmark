# Benchmarking Report

A benchmarking report is a standard data format describing the cluster configuration, workload, and results of a benchmark run. The report acts as a common API for different benchmarking experiments. Each supported harness in llm-d-benchmark creates a benchmark report upon completion of a run, in addition to saving results in its native format.

## Format Description

A benchmark report describes the inference service configuration, workload, and aggregate results. Individual traces from single inference executions are not captured, rather statistics from multiple traces of identical scenarios are combined to create a report.

A [JSON Schema](https://json-schema.org/draft/2020-12) for the benchmark report is in [`report_json_schema.json`](report_json_schema.json). The report has three top-level fields, `version`, `scenario`, and `metrics`.

While each of these fields is required, some subfields may be optional or not apply to the specific benchmark being performed. For example, some metrics may not be captured or supported by a certain benchmarking toolset. In cases where one desires to capture information that is not part of the standard benchmark report schema, a `metadata` field may be placed almost anywhere under `scenario` or `metrics` to add arbitrary data.

### `version` Field

The `version` field is used to track the specific data format. Should the schema change with future revisions, this field will identify the specific format used (with, for example, a corresponding JSON Schema).

### `scenario` Field

The `scenario` describes precisely what was measured. This includes details about the inference platform (full stack, including versions and the important runtime arguments), cluster configuration (like GPUs and parallelism utilized), and workload. The content in this field should be detailed enough that it is sufficient to launch a repeat benchmarking experiment that will yield similar results (within some reasonable bound of variability).

> [!NOTE]
> With future revisions the `scenario` field could be used as an input format for executing benchmark runs. For this to be practical we would first need to standardize the definition of a workload, specifically in `scenario.load.args` which is currently the exact arguments (non-standard) sent to a particular harness. In addition, the input format would need a way to describe swept parameters. A benchmark report currently describes a single point in any sweep.

### `metrics` Field

The `metrics` field contains all of the results for the report. This does not include individual trace details, rather statistics for all runs that were captured in benchmarking for a particular scenario. This includes request-level performance metrics (like latencies and throughput), details about the inference service (like request queue lengths and KV cache size), and hardware metrics (such as GPU compute and memory utilization). Some of the underlying fields, such as the hardware metrics, more traditionally fall in the category of "observability" rather than "benchmarking".

### Example Report

The following report is primarily an illustration of the schema. The values within are filler material, and may be nonsensical.

```yaml
version: '0.1' # Apply a version that updates with schema changes
scenario: # This section provides the specific environment and workload
  description: This is a heterogeneous accelerator setup with two lora adapters
  host:
    type: # This will either be all "replica" or a mix of "prefill" and "decode"
      - prefill
      - decode
      - decode
    accelerator: # This is heterogeneous across prefill and decode, with 1 prefill and 2 decode (defined in scenario.host.type)
      - model: H100 # Prefill
        memory: 80
        count: 1
        parallelism:
          dp: 1
          tp: 1
          pp: 1
          ep: 1
      - model: H100 # First decode
        memory: 80
        count: 8
        parallelism:
          dp: 1
          tp: 8
          pp: 1
          ep: 8
      - model: H100 # Second decode
        memory: 80
        count: 8
        parallelism:
          dp: 1
          tp: 8
          pp: 1
          ep: 8
  platform:
    engine: # This list correlates 1:1 with the items listed in scenario.host.accelerator
      - name: vllm # Prefill
        version: 0.9.0.1
        args:
          "--dtype": fp16
          "--tensor-parallel-size": 1
          "--pipeline-parallel-size": 1
          "--enable-expert-parallel": true
          "--data-parallel-size": 1
          "--data-parallel-size-local": 1
      - name: vllm # First decode
        version: 0.9.0.1
        args:
          "--dtype": fp16
          "--tensor-parallel-size": 8
          "--pipeline-parallel-size": 1
          "--enable-expert-parallel": true
          "--data-parallel-size": 3
          "--data-parallel-size-local": 1
          "--data-parallel-address": 10.12.33.212
          "--data-parallel-rpc-port": 5555
          "--data-parallel-start-rank": 1
      - name: vllm # Second decode
        version: 0.9.0.1
        args:
          "--dtype": fp16
          "--tensor-parallel-size": 8
          "--pipeline-parallel-size": 1
          "--enable-expert-parallel": true
          "--data-parallel-size": 3
          "--data-parallel-size-local": 1
          "--data-parallel-address": 10.12.33.212
          "--data-parallel-rpc-port": 5555
          "--data-parallel-start-rank": 2
  model:
    name: deepseek-ai/DeepSeek-R1-0528
    quantization: fp16
    adapters:
    - lora: sql_adapter
    - lora: golang_adapter
  load:
    name: inference-perf
    type: long-input
    args: # This section is currently unique to each harness. If this can be standardized, it may serve as a universal input to launching benchmark runs.
      qps_values: 1.34
      num_users_warmup: 20
      num_users: 15
      num_rounds: 20
      system_prompt: 1000
      chat_history: 20000
      answer_len: 100
      test_duration: 100
      use_chat_completions: false
metrics: # These are the aggregate results from benchmarking
  time:
    duration: 16.531641244888306
    start: 1749570583.5714512 # UTC seconds from epoch
    stop: 1749570580.1030924
  requests:
    total: 32
    failures: 0
    incomplete: 1
    input_length:
      units: count
      mean: 628.606060606061
      stddev: 19.8353456345
      min: 4
      p10: 11
      p50: 364
      p90: 2427
      max: 3836
    output_length:
      units: count
      mean: 31.7878787878788
      stddev: 19.8353456345
      min: 30
      p10: 31
      p50: 32
      p90: 32
      max: 32
  latency:
    request_latency:
      units: ms
      mean: 3.31325431142327
      stddev: 0.00198353456345
      min: 1.62129471905064
      p10: 1.67609986825846
      p50: 2.11507539497688
      p90: 5.94717199734878
      max: 6.30658466403838
    normalized_time_per_output_token:
      units: ms/token
      mean: 0.104340420636009
      stddev: 0.00198353456345
      min: 0.0506654599703325
      p10: 0.0523781208830769
      p50: 0.0670631669655753
      p90: 0.189047570470012
      max: 0.20343821496898
    time_per_output_token:
      units: ms/token
      mean: 0.0836929455635872
      stddev: 0.00198353456345
      min: 0.0517028436646797
      p10: 0.0530815053513894
      p50: 0.0611870964678625
      p90: 0.152292036800645
      max: 0.17837208439984
    time_to_first_token:
      units: ms
      mean: 0.800974442732916
      stddev: 0.00198353456345
      min: 0.0625283779809251
      p10: 0.072068731742911
      p50: 0.203539535985328
      p90: 2.26959549135063
      max: 4.46773961000145
    inter_token_latency:
      units: ms/token
      mean: 0.0836929455635872
      stddev: 0.00198353456345
      min: 7.129972800612e-06
      p10: 0.0534287681337446
      p50: 0.0591336835059337
      p90: 0.084046097996179
      max: 0.614475268055685
  throughput:
    input_tokens_per_sec: 643.576644186323
    output_tokens_per_sec: 32.544923821416
    total_tokens_per_sec: 676.121568007739
    requests_per_sec: 1.0238155253639
  service: # These are metrics about the inference service
    batch_size:
      units: count
      mean: 234.23049
      stddev: 34.12342
      min: 123
      p10: 143
      p50: 533
      p90: 625
      max: 753
    queue_size:
      units: count
      mean: 234.12451
      stddev: 34.56737
      min: 123
      p10: 143
      p50: 533
      p90: 625
      max: 753
    kv_cache_size:
      units: count
      mean: 2194993.253
      stddev: 2342.3456
      min: 1194345
      p10: 1394456
      p50: 2404751
      p90: 2534437
      max: 2554393
  resources: # These are hardware level metrics
    accelerator: # This list correlates 1:1 with the items listed in scenario.host.accelerator
      - memory: # This corresponds to the prefill pod
          consumption:
            units: MB
            mean: 2194993.2346
            stddev: 2342.4568
            min: 1194345
            p10: 1394456
            p50: 2404751
            p90: 2534437
            max: 2554393
          utilization:
            units: percent
            mean: 80.235
            stddev: 32.1
            min: 40.3
            p10: 44.4
            p50: 71.3
            p90: 97.1
            max: 99.2
          bandwidth:
            units: MB/s
            mean: 21993.2346
            stddev: 22.4568
            min: 19445.2347
            p10: 13456.5367
            p50: 24051.2456
            p90: 24437.4582
            max: 25543.3457
        compute:
          utilization:
            units: percent
            mean: 40.56
            stddev: 12.15
            min: 20.3
            p10: 24.4
            p50: 31.3
            p90: 47.1
            max: 49.2
        power:
          units: Watts
          mean: 410.02
          stddev: 170.1
          min: 201.3
          p10: 243.4
          p50: 314.3
          p90: 475.1
          max: 497.2
      - memory: # This corresponds to the first decode pod
          consumption:
            units: MB
            mean: 2194993.2346
          utilization:
            units: percent
            mean: 80.235
          bandwidth:
            units: MB/s
            mean: 21993.2346
        compute:
          utilization:
            units: percent
            mean: 40.56
        power:
          units: Watts
          mean: 410.02
      - memory: # This corresponds to the second decode pod
          consumption:
            units: MB
            mean: 2194993.2346
          utilization:
            units: percent
            mean: 80.235
          bandwidth:
            units: MB/s
            mean: 21993.2346
        compute:
          utilization:
            units: percent
            mean: 40.56
        power:
          units: Watts
          mean: 410.02
```

## Implementation and Usage

The schema for a benchmarking report is defined through Python classes using [Pydantic](https://docs.pydantic.dev/latest/) in [schema.py](schema.py), where the base class is `BenchmarkReport`. If [schema.py](schema.py) is executed directly, the JSON Schema for the benchmark report will be printed out. Instantiating an instance of `BenchmarkReport` includes various checks, such as ensuring compliance with the schema, proper use of units, and defining all required entities.

### Requirements

```
numpy>=2.3.1
pydantic>=2.11.7
PyYAML>=6.0.2
scipy>=1.16.0
```

### Creating a `BenchmarkReport`

An instance of `BenchmarkReport` may be created directly, for example:
```python
br = schema.BenchmarkReport(**{
    "scenario": {
        "model": {"name": "deepseek-ai/DeepSeek-R1-0528"},
        "load": {"name": schema.WorkloadGenerator.INFERENCE_PERF},
        "host": {
            "accelerator": [{"model": "H100", "memory": 80, "count": 3}, {"model": "H100", "memory": 80, "count": 3}],
            "type": ["prefill", "decode"]
        },
        "platform": {"engine": [{"name": "vllm", "args": {}}, {"name": "vllm", "args": {}}]},
    },
    "metrics": {
        "time": {"duration": 10.3},
        "requests": {
            "total": 58,
            "input_length": {
                "units": schema.Units.COUNT,
                "mean": 1000,
            },
            "output_length": {
                "units": schema.Units.COUNT,
                "mean": 20000,
            },
        },
        "latency": {
            "time_to_first_token": {
                "units": schema.Units.MS,
                "mean": 3.4,
            },
        },
        "throughput": {"total_tokens_per_sec": 30.4},
        "resources": {"accelerator": [{"power": {"units": schema.Units.WATTS, "mean": 9.3}}, {"power": {"units": schema.Units.WATTS, "mean": 9.3}}]},
    },
})
```

A `BenchmarkReport` may also be created from a JSON/YAML string with the `schema.create_from_str()` function. A JSON/YAML file may be imported as a `dict` with the `convert.import_yaml()` function, and this `dict` can then be unpacked to create a `BenchmarkReport`.
```python
br = BenchmarkReport(**convert.import_yaml('benchmark_report.json'))
```

A JSON or YAML printout of `BenchmarkReport` may be generated the `print_json()` and `print_yaml()` methods, respectively. To save as a JSON/YAML file, use the `export_json()` or `export_yaml()` methods.

### Transforming harness native formats to a benchmark report

The native formats returned by different harnesses may be converted to a benchmark report using [convert.py](convert.py). This file when executed directly as a script will import the native results data of a harness and print to `stdout` a benchmark report, or save a report to file if a second argument is provided. [convert.py](convert.py) can also be used as a library, to import results files as a `BenchmarkReport` object. This is done, for example, in the analysis Jupyter notebook [`analysis_pd.ipynb`](../../analysis/analysis_pd.ipynb).
