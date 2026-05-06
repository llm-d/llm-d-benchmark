"""Storage clients package for result store."""

from llmdbenchmark.result_store.client.base import StorageClient
from llmdbenchmark.result_store.client.gcs import GCSClient
from llmdbenchmark.result_store.client.gcs_proxy import GCSProxyClient

def get_storage_client(uri: str) -> StorageClient:
    """Factory to return appropriate StorageClient based on URI scheme."""
    if uri.startswith("gs://"):
        return GCSClient()
    raise ValueError(f"Unsupported storage URI scheme: {uri}")

def get_fallback_client(primary_client: StorageClient) -> StorageClient:
    """Returns the appropriate fallback client for a given primary client."""
    if isinstance(primary_client, GCSClient):
        return GCSProxyClient()
    return None
