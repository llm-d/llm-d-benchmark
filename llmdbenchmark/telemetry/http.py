import json
import sys
import requests
import threading
import queue
from llmdbenchmark.telemetry.interface import TelemetryProvider
from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger

class HttpTelemetryProvider(TelemetryProvider):
    """HTTP POST implementation of TelemetryProvider with a background queue worker."""

    def __init__(self, endpoint: str, logger=None, bearer_token=lambda: None):
        self.endpoint = endpoint
        self.bearer_token = bearer_token
        self.logger = logger or get_logger(
            config.log_dir, verbose=config.verbose, log_name=__name__
        )
        
        self.queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def push(self, data: dict) -> None:
        """Put telemetry data into the queue."""
        if not self.endpoint:
            return
        self.queue.put(data)

    def _worker(self) -> None:
        """Background worker that processes the queue."""
        while True:
            try:
                data = self.queue.get()
                if data is None:
                    # Sentinel value to stop the worker
                    self.queue.task_done()
                    break
                
                self._push_payload(data)
                self.queue.task_done()
            except Exception as e:
                self.logger.log_error(f"[telemetry] Error in worker thread: {e}")

    def _push_payload(self, data: dict) -> None:
        headers = {
            "Content-Type": "application/json",
        }

        if token := self.bearer_token():
            headers["Authorization"] = token

        try:
            # We use a timeout to avoid blocking forever if the endpoint is hung
            response = requests.post(
                self.endpoint,
                json=data,
                headers=headers,
                timeout=30.0
            )
            if response.status_code != 200 and response.status_code != 201:
                self.logger.log_error(
                    f"[telemetry] Failed to push telemetry, status code: {response.status_code}"
                )
            else:
                self.logger.log_info("[telemetry] Telemetry pushed successfully.", emoji="📊")
        except requests.Timeout:
            self.logger.log_error("[telemetry] Timeout pushing telemetry.")
        except requests.RequestException as e:
            self.logger.log_error(f"[telemetry] Failed to push telemetry: {e}")

    def stop(self) -> None:
        """Stop the background worker and wait for it to finish."""
        self.queue.put(None)
        self.worker_thread.join()


