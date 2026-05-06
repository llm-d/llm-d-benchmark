"""GCS client wrapper for results store."""

import os
from pathlib import Path
from google.cloud import storage
from llmdbenchmark.result_store.client.base import StorageClient

def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Parses gs://bucket/prefix into (bucket, prefix)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    parts = uri[5:].split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket, prefix

class GCSClient(StorageClient):
    """Handles pure GCS operations using standard credentials."""

    def __init__(self):
        self.client = storage.Client()

    def ls(self, uri: str) -> list[str]:
        """Lists object names under the given URI."""
        bucket_name, prefix = parse_gcs_uri(uri)
        bucket = self.client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)
        return [blob.name for blob in blobs]

    def push(self, uri: str, local_dir: str) -> int:
        """Pushes all files in a local directory to the remote URI."""
        bucket_name, dest_prefix = parse_gcs_uri(uri)
        bucket = self.client.bucket(bucket_name)
        local_path = Path(local_dir)

        if not local_path.exists() or not local_path.is_dir():
            raise ValueError(f"Local directory '{local_dir}' does not exist or is not a directory.")

        uploaded_count = 0
        for root, _, files in os.walk(local_path):
            for file in files:
                file_path = Path(root) / file
                relative_path = file_path.relative_to(local_path)
                blob_path = f"{dest_prefix}/{relative_path}".replace("//", "/")
                if blob_path.startswith("/"):
                    blob_path = blob_path[1:]
                
                blob = bucket.blob(blob_path)
                blob.upload_from_filename(str(file_path))
                uploaded_count += 1

        return uploaded_count

    def exists(self, uri: str) -> bool:
        """Checks if any object exists with the given URI prefix."""
        bucket_name, prefix = parse_gcs_uri(uri)
        bucket = self.client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=prefix, max_results=1))
        return len(blobs) > 0

    def pull(self, uri: str, dest_dir: str) -> int:
        """Pulls all objects from URI to dest_dir."""
        bucket_name, prefix = parse_gcs_uri(uri)
        bucket = self.client.bucket(bucket_name)
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)

        downloaded_count = 0
        blobs = bucket.list_blobs(prefix=prefix)
        for blob in blobs:
            relative_name = blob.name[len(prefix):].lstrip("/")
            if not relative_name:
                continue
            file_dest = dest_path / relative_name
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(file_dest))
            downloaded_count += 1

        return downloaded_count
