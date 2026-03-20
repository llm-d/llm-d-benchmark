# LLM-D Stack Configuration Discovery Tool

A Python tool for discovering and collecting configuration details from an llm-d stack given an OpenAI endpoint URL. The tool traces through the endpoint to discover all stack components and outputs configuration compatible with llm-d-benchmark's v0.2 report schema.

## Features

- **URL-based Discovery**: Start from an OpenAI-compatible endpoint URL and trace through the entire stack
- **Multi-platform Support**: Works with both Kubernetes and OpenShift clusters
- **Component Detection**: Automatically identifies and collects configuration from:
  - OpenShift Routes
  - Gateway API Gateways, HTTPRoutes
  - GAIE InferencePools and controllers
  - vLLM inference engines
  - Kubernetes Services, Pods, ConfigMaps
- **Multiple Output Formats**: JSON, YAML, human-readable summary, native config (JSON/YAML), and benchmark report
- **Benchmark Report Compatibility**: Export in llm-d-benchmark v0.2 report format
- **Component Filtering**: Filter output by component type

## Installation

### Local Installation

1. Clone the repository and navigate to the tool directory:
```bash
cd llm-d-benchmark/llm_d_stack_discovery
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Install the tool:
```bash
pip install -e .
```

### Container Installation

Build the container image from the repository root:
```bash
docker build -f llm_d_stack_discovery/Dockerfile -t llm-d-stack-discovery:latest .
```

## Usage

### Basic Usage

Discover stack configuration from an endpoint URL:
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1
```

### With Custom Kubeconfig

```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --kubeconfig ~/.kube/config \
  --context my-cluster
```

### Output Formats

JSON output:
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --output-format json \
  --output config.json
```

YAML output:
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --output-format yaml
```

Human-readable summary (default):
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1
```

Native configuration format (JSON):
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --output-format native \
  --output config.json
```

Native configuration format (YAML):
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --output-format native-yaml
```

The `native` and `native-yaml` formats provide raw configuration details including:
- Command arguments and environment variables
- Resource specifications
- Pod and service configurations
- Native Kubernetes manifests
- No transformation into standardized schema

### Export for Benchmark Reports

Export in llm-d-benchmark v0.2 format:
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --output-format benchmark-report \
  --output benchmark-config.json
```

### Filter Components

Filter by component type:
```bash
# Show only vLLM pods
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --filter vllm

# Show only Services
python -m llm_d_stack_discovery.cli https://model.example.com/v1 \
  --filter Service
```

### Verbose Mode

Enable detailed logging:
```bash
python -m llm_d_stack_discovery.cli https://model.example.com/v1 --verbose
```

## Kubernetes Deployment

1. Apply RBAC permissions:
```bash
kubectl apply -f k8s/rbac.yaml
```

2. Run as a Job:
```bash
# Edit k8s/job.yaml to set your URL
kubectl apply -f k8s/job.yaml

# Check results
kubectl logs job/llm-d-stack-discovery
```

## How It Works

1. **URL Parsing**: The tool parses the provided OpenAI endpoint URL to extract host and port information

2. **Entry Point Discovery**: It searches for the entry point by checking:
   - OpenShift Routes (by hostname)
   - Gateway API Gateways (by listener hostname)
   - Kubernetes Services (LoadBalancer/NodePort by endpoint)

3. **Stack Tracing**: Starting from the entry point, it traces through:
   - Routes → Services → Pods
   - Gateways → HTTPRoutes → Services/InferencePools → Pods
   - InferencePools → Backend Services → Pods (or Pods directly via modelServers selector)

4. **Configuration Collection**: For each component, it collects:
   - Metadata (name, namespace, labels, annotations)
   - Standardized configuration (following v0.2 schema)
   - Native Kubernetes resource configuration

5. **Output Generation**: Results are formatted according to the requested format

## Component Detection

### vLLM Inference Engines
- Detects vLLM pods by command, args, and image name
- Extracts model name, parallelism settings (TP, PP, DP, EP)
- Determines role (prefill, decode, replica)
- Collects GPU information from node labels

### GAIE Components
- InferencePools with routing and backend configuration
- GAIE controller pods and their settings
- Plugin configurations from ConfigMaps

### Gateway Components
- OpenShift Routes with TLS and backend configuration
- Gateway API Gateways with listener settings
- HTTPRoutes with routing rules and backend references

## Output Schema

The default JSON format (`--output-format json`) outputs components as follows:

```json
{
  "url": "https://model.example.com/v1",
  "timestamp": "2024-01-20T10:30:00Z",
  "cluster_info": {
    "platform": "openshift",
    "version": "4.14.0"
  },
  "components": [
    {
      "metadata": {
        "namespace": "llm-d-models",
        "name": "vllm-model-0",
        "kind": "Pod",
        "labels": {},
        "annotations": {}
      },
      "tool": "vllm",
      "tool_version": "0.3.0",
      "native": {}
    }
  ],
  "errors": []
}
```

## Troubleshooting

### Permission Errors
If you see permission errors, ensure your kubeconfig has read access to:
- Pods, Services, ConfigMaps, Nodes
- Routes (OpenShift)
- Gateways, HTTPRoutes (Gateway API)
- InferencePools (GAIE)

### URL Not Found
If the tool cannot find the entry point:
1. Verify the URL is accessible from your location
2. Check that the hostname matches exactly in Routes/Gateways
3. For NodePort services, ensure the port matches

### Missing Components
Some components might not be discovered if:
- They're in a different namespace without proper references
- Label selectors don't match
- Resources have been deleted or are being updated

## Development

### Running Tests

From the repository root:
```bash
python -m pytest llm_d_stack_discovery/tests/
```

Or from within the `llm_d_stack_discovery/` directory:
```bash
python -m pytest tests/
```

### Adding New Collectors
1. Create a new collector class inheriting from `BaseCollector`
2. Implement the `collect()` method
3. Add detection logic to the tracer
4. Update the documentation

## License

This tool is part of the llm-d-benchmark project and follows the same license terms.