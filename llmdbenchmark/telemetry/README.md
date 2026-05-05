# Telemetry

This module provides a non-blocking, asynchronous telemetry system to push benchmark results and metadata to a central receiver.

## Architecture

The telemetry system consists of:

1.  **`init_telemetry`**: A singleton initialization function in `__init__.py` that sets up the provider based on configuration.
2.  **`HttpTelemetryProvider`**: An HTTP POST implementation that uses a background queue worker to send data without blocking the main execution thread.
3.  **Auth Providers**: Pluggable authentication providers. Currently supports Google OIDC token impersonation via `google_auth_provider.py`.

## Configuration

Telemetry is configured via environment variables or command-line flags:

*   `LLMDBENCH_TELEMETRY_ENABLED`: Set to `1` or `true` to enable telemetry.
*   `LLMDBENCH_TELEMETRY_ENDPOINT`: The full URL of the receiver endpoint (e.g., `https://receiver/telemetry`).
*   `LLMDBENCH_TELEMETRY_TOKEN`: Optional. A raw OIDC ID token to use for authentication. The client will automatically prefix it with `Bearer `.
*   `LLMDBENCH_TELEMETRY_AUTH_PROVIDER`: Set to `google` to automatically fetch OIDC tokens using Google Default Credentials.

## Usage

To use telemetry in local tests against a Cloud Run receiver requiring authentication:

```bash
LLMDBENCH_TELEMETRY_TOKEN=$(gcloud auth print-identity-token --impersonate-service-account="YOUR_SA@YOUR_PROJECT.iam.gserviceaccount.com" --audiences="https://your-receiver-url.run.app") \
llmdbenchmark --spec your-spec \
  --telemetry-enabled \
  --telemetry-provider=http \
  --telemetry-endpoint https://your-receiver-url.run.app/telemetry \
  run ...
```
