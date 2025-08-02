# Benchmarking Report

Example report
```yaml
version: '0.1' # Apply a version that updates with schema changes
scenario: # This section provides the specific environment and workload
  description: This is a heterogeneous accelerator setup with two lora adapters
  host:
    type:
      - prefill
      - decode
      - decode
    accelerator: # This is heterogeneous across prefill and decode, with 1 prefill and 2 decode
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
  load: # Unsure about best format here... in principle this should contain enough information to execute a load generator
    name: inference-perf
    type: long-input
    args:
      qps_values: 1.34
      num_users_warmpup: 20
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

Example construction of a `BenchmarkRun` object instance
```python
br = BenchmarkRun(**{
    "scenario": {
        "model": {"name": "deepseek-ai/DeepSeek-R1-0528"},
        "load": {"name": WorkloadGenerator.INFERENCE_PERF},
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
                "units": Units.COUNT,
                "mean": 1000,
            },
            "output_length": {
                "units": Units.COUNT,
                "mean": 20000,
            },
        },
        "latency": {
            "time_to_first_token": {
                "units": Units.MS,
                "mean": 3.4,
            },
        },
        "throughput": {"total_tokens_per_sec": 30.4},
        "resources": {"accelerator": [{"power": {"units": Units.WATTS, "mean": 9.3}}, {"power": {"units": Units.WATTS, "mean": 9.3}}]},
    },
})
```
