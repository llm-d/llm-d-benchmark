"""GCS client wrapper for results store."""

import os
from pathlib import Path
import yaml
import fnmatch
from google.cloud import storage

class GCSClient:
    """Handles GCS list, push, and pull operations using standard credentials."""

    def __init__(self):
        self.client = storage.Client()

    def _parse_uri(self, uri: str) -> tuple[str, str]:
        """Parses gs://bucket/prefix into (bucket, prefix)."""
        if not uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {uri}")
        parts = uri[5:].split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        return bucket, prefix

    def ls(self, uri: str, model: str = None, hardware: str = None) -> list:
        """
        Lists benchmark runs.
        Returns a list of dicts describing the runs found.
        """
        try:
            bucket_name, base_prefix = self._parse_uri(uri)
            bucket = self.client.bucket(bucket_name)

            blobs = bucket.list_blobs(prefix=base_prefix)
            runs = []
            for blob in blobs:
                if blob.name.endswith("report_v0.2.yaml"):
                    rel_name = blob.name
                    if base_prefix:
                        prefix_check = base_prefix if base_prefix.endswith("/") else base_prefix + "/"
                        if rel_name.startswith(prefix_check):
                            rel_name = rel_name[len(prefix_check):]
                    
                    parts = rel_name.split("/")
                    
                    if len(parts) >= 6:
                        # Format: group/scenario/model.../hardware/run_uid/report
                        group = parts[0]
                        scen = parts[1]
                        mod = "/".join(parts[2:-3])
                        hw = parts[-3]
                        run_uid = parts[-2]
                    else:
                        continue

                    if model:
                        if '*' in model or '?' in model:
                            if not fnmatch.fnmatch(mod, model):
                                continue
                        elif model != mod:
                            continue
                            
                    if hardware:
                        if '*' in hardware or '?' in hardware:
                            if not fnmatch.fnmatch(hw, hardware):
                                continue
                        elif hardware != hw:
                            continue

                    runs.append({
                        "run_uid": run_uid,
                        "scenario": scen,
                        "model": mod,
                        "hardware": hw,
                        "group": group,
                        "blob_name": blob.name
                    })
            return runs
        except Exception as e:
            raise RuntimeError(f"GCS ls failed: {e}")

    def push(self, uri: str, local_dir: str, metadata: dict, group: str = "default") -> str:
        """Pushes a local directory to the remote using provided metadata."""
        local_path = Path(local_dir)
        
        scen = metadata.get("scenario", "missing")
        mod = metadata.get("model", "missing")
        hw = metadata.get("hardware", "missing")
        run_uid = metadata.get("run_uid", "missing")

        bucket_name, base_prefix = self._parse_uri(uri)
        bucket = self.client.bucket(bucket_name)

        dest_prefix = f"{base_prefix}/{group}/{scen}/{mod}/{hw}/{run_uid}".replace("//", "/")
        if dest_prefix.startswith("/"):
            dest_prefix = dest_prefix[1:]

        report_file = None
        for root, _, files in os.walk(local_path):
            for file in files:
                if file.startswith("benchmark_report_v0.2") and file.endswith(".yaml"):
                    report_file = Path(root) / file
                    break
            if report_file:
                break
                
        if not report_file:
            raise FileNotFoundError(f"Could not find benchmark_report_v0.2*.yaml in {local_path}")
            
        blob_path = f"{dest_prefix}/report_v0.2.yaml"
        blob = bucket.blob(blob_path)
        blob.upload_from_filename(str(report_file))

        return f"gs://{bucket_name}/{dest_prefix}"

    def exists(self, uri: str, run_metadata: dict, group: str = "default") -> bool:
        """Checks if a run already exists in remote."""
        bucket_name, base_prefix = self._parse_uri(uri)
        bucket = self.client.bucket(bucket_name)
        
        scen = run_metadata.get("scenario", "missing")
        mod = run_metadata.get("model", "missing")
        hw = run_metadata.get("hardware", "missing")
        run_uid = run_metadata.get("run_uid", "missing")
        
        dest_prefix = f"{base_prefix}/{group}/{scen}/{mod}/{hw}/{run_uid}".replace("//", "/")
        if dest_prefix.startswith("/"):
            dest_prefix = dest_prefix[1:]
            
        blobs = list(bucket.list_blobs(prefix=dest_prefix, max_results=1))
        return len(blobs) > 0

    def pull(self, uri: str, run_uid: str, dest_dir: str) -> tuple[str, int]:
        """Pulls a specific run_uid bundle to dest_dir."""
        bucket_name, base_prefix = self._parse_uri(uri)
        bucket = self.client.bucket(bucket_name)

        # Naively search for run_uid traversing the tree.
        blobs = bucket.list_blobs(prefix=base_prefix)
        target_prefix = None
        
        for blob in blobs:
             parts = blob.name.split("/")
             if run_uid in parts:
                 idx = parts.index(run_uid)
                 target_prefix = "/".join(parts[:idx+1])
                 break

        if not target_prefix:
            raise ValueError(f"Run UID '{run_uid}' not found in {uri}")

        dest_path = Path(dest_dir) / run_uid
        dest_path.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        for blob in bucket.list_blobs(prefix=target_prefix):
            rel_name = blob.name[len(target_prefix):].lstrip("/")
            if not rel_name:
                continue
            file_dest = dest_path / rel_name
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(file_dest))
            downloaded += 1

        return str(dest_path), downloaded

    def get_report(self, uri: str, scenario: str, model: str, hardware: str = None) -> dict:
        """Fetches a report directly for diffing."""
        bucket_name, base_prefix = self._parse_uri(uri)
        bucket = self.client.bucket(bucket_name)

        prefix = f"{base_prefix}/{scenario}/{model}".replace("//", "/")
        if prefix.startswith("/"):
            prefix = prefix[1:]
            
        blobs = bucket.list_blobs(prefix=prefix)
        for blob in blobs:
            if blob.name.endswith("report_v0.2.yaml"):
                if hardware and f"/{hardware}/" not in blob.name:
                    continue
                content = blob.download_as_string()
                return yaml.safe_load(content)

        return None
