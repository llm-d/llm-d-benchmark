"""GCS Proxy client for fallback access when direct GCS access fails."""

import os
import urllib.parse
import requests
from llmdbenchmark.exceptions.exceptions import ConfigurationError
from llmdbenchmark.result_store.client.base import StorageClient
from llmdbenchmark.result_store.client.gcs import parse_gcs_uri
from pathlib import Path

class GCSProxyClient(StorageClient):
    """Handles pure GCS operations via Prism proxy fallback."""

    def __init__(self):
        self.prism_url = os.environ.get("LLMDBENCH_PRISM_URL", "https://prism.llm-d.ai").rstrip("/")
        if not self.prism_url:
             raise ConfigurationError(
                 message="LLMDBENCH_PRISM_URL is required for proxy fallback but is empty.",
                 step="result_store",
             )
        self.session = requests.Session()

    def _get_bucket_uri(self, bucket: str) -> str:
        return f"{self.prism_url}/api/gcs/storage/v1/b/{bucket}"

    def ls(self, uri: str) -> list[str]:
        """Lists object names under the given URI."""
        bucket_name, prefix = parse_gcs_uri(uri)
        url = f"{self._get_bucket_uri(bucket_name)}/o"
        params = {}
        if prefix:
            params["prefix"] = prefix

        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            items = data.get("items", [])
            return [item["name"] for item in items if "name" in item]
        except Exception as exception:
            raise RuntimeError(f"Proxy GCS ls failed: {exception}")

    def push(self, uri: str, local_dir: str) -> int:
        """Push is not supported via proxy."""
        raise NotImplementedError("Push operations are not supported via GCS proxy.")

    def exists(self, uri: str) -> bool:
        """Checks if any object exists with the given URI prefix."""
        bucket_name, prefix = parse_gcs_uri(uri)
        url = f"{self._get_bucket_uri(bucket_name)}/o"
        params = {"prefix": prefix, "maxResults": 1}
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return len(data.get("items", [])) > 0
        except Exception as exception:
            raise RuntimeError(f"Proxy GCS exists failed: {exception}")

    def pull(self, uri: str, dest_dir: str) -> int:
        """Pulls all objects from URI to dest_dir."""
        bucket_name, prefix = parse_gcs_uri(uri)
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)
        
        url = f"{self._get_bucket_uri(bucket_name)}/o"
        params = {"prefix": prefix}
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            items = data.get("items", [])
            
            downloaded_count = 0
            for item in items:
                name = item.get("name")
                if name:
                    relative_name = name[len(prefix):].lstrip("/")
                    if not relative_name:
                        continue
                    file_dest = dest_path / relative_name
                    file_dest.parent.mkdir(parents=True, exist_ok=True)
                    
                    encoded_name = urllib.parse.quote(name, safe="")
                    media_url = f"{self._get_bucket_uri(bucket_name)}/o/{encoded_name}"
                    media_response = self.session.get(media_url, params={"alt": "media"}, timeout=30)
                    media_response.raise_for_status()
                    
                    with open(file_dest, "wb") as f:
                        f.write(media_response.content)
                    downloaded_count += 1
                    
            return downloaded_count
        except Exception as exception:
            raise RuntimeError(f"Proxy GCS pull failed: {exception}")
