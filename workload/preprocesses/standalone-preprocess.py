#!/usr/bin/env python3

"""Serialize tensorizer file if needed"""

import json
import logging
import multiprocessing
import os
import subprocess
import sys
import threading
import time

import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def kill_process(proc: psutil.Process):
    """kills a process"""
    for child in proc.children(recursive=True):
        if child.is_running():
            child.kill()
            logger.info("child process %d terminated", child.pid)

    if proc.is_running():
        proc.kill()
        logger.info("main process %d terminated", proc.pid)


def vllm_health(
    process: multiprocessing.Process | subprocess.Popen,
    health_counter: list[float],
    health_counter_lock: threading.Lock,
    end_event: threading.Event,
):
    """makes sure vllm process is not stuck"""

    max_health_wait = 15 * 60

    proc = psutil.Process(process.pid)
    while not end_event.is_set() and proc.is_running():
        time.sleep(0.5)

        start = 0.0
        with health_counter_lock:
            start = health_counter[0]

        elapsed = time.perf_counter() - start
        if elapsed > max_health_wait:
            # if vllm hasn't responded
            logger.info(
                "vLLM process is stuck for more than %.2f secs, aborting ...", elapsed
            )
            kill_process(proc)
            return


class PipeTee:
    """sends stdout to pipe"""

    def __init__(self, pipe):
        self.pipe = pipe
        self.stdout = sys.stdout
        sys.stdout = self

    def write(self, data):
        """writes data"""
        self.stdout.write(data)
        self.pipe.send(data)

    def flush(self):
        """flushes data"""
        self.stdout.flush()

    def __del__(self):
        sys.stdout = self.stdout
        self.stdout = None


def serialize(model: str, tensorizer_uri: str, conn) -> None:
    """serializes a model to disk"""

    _ = PipeTee(conn)

    from vllm.engine.arg_utils import EngineArgs
    from vllm.model_executor.model_loader.tensorizer import (
        TensorizerConfig,
        tensorize_vllm_model,
    )

    engine_args = EngineArgs(model=model)
    tensorizer_config = TensorizerConfig(tensorizer_uri=tensorizer_uri)

    tensorize_vllm_model(engine_args, tensorizer_config)


def serialize_model(model: str, tensorizer_uri: str) -> None:
    """process to serialize a model to disk"""

    parent_conn, child_conn = multiprocessing.Pipe()

    process = multiprocessing.Process(
        target=serialize,
        args=(model, tensorizer_uri, child_conn),
    )
    process.start()

    # Close the write end of the pipe in the parent process
    child_conn.close()

    health_counter = [time.perf_counter()]
    health_counter_lock = threading.Lock()
    end_health_event = threading.Event()

    health_thread = threading.Thread(
        target=vllm_health,
        args=(process, health_counter, health_counter_lock, end_health_event),
        daemon=True,
    )
    health_thread.start()

    while process.is_alive() or parent_conn.poll():
        if parent_conn.poll():
            try:
                _ = parent_conn.recv()
                # restart health counter
                with health_counter_lock:
                    health_counter[0] = time.perf_counter()
            except EOFError:
                break  # Exit loop when pipe is closed

    # Wait for the child process to finish
    process.join()
    parent_conn.close()
    end_health_event.set()  # end health check event
    health_thread.join()

    if process.exitcode is not None and process.exitcode != 0:
        raise RuntimeError(f"Serialize process exited with code '{process.exitcode}'")


def get_env_variables(dicts: list[dict]) -> list[str]:
    """get environment variables"""

    logger.info("Environment variables:")

    env_vars = os.environ

    envs = []
    missing_envs = []
    for env_dict in dicts:
        name = env_dict["name"]
        value = env_vars.get(name)
        if value is None and env_dict["required"]:
            missing_envs.append(name)
        else:
            envs.append(value)
            logger.info("  '%s': '%s'", name, value)

    if len(missing_envs) > 0:
        raise RuntimeError(f"Env. variables not found: {','.join(missing_envs)}.")
    return envs


def logging_config(path: str) -> None:
    """create custom logging config"""

    # first clear config path env.
    if "VLLM_LOGGING_CONFIG_PATH" in os.environ:
        del os.environ["VLLM_LOGGING_CONFIG_PATH"]

    from vllm.logger import DEFAULT_LOGGING_CONFIG

    json_data = DEFAULT_LOGGING_CONFIG
    formatters = json_data.get("formatters")
    if formatters is not None:
        vllm_formatter = formatters.get("vllm")
        if vllm_formatter is not None:
            format_str = vllm_formatter.get("format")
            if format_str is not None:
                vllm_formatter["format"] = format_str.replace(
                    "%(asctime)s", "%(asctime)s.%(msecs)03d"
                )

    # change default config
    json_string = json.dumps(json_data, indent=4)
    logger.info("custom log config: %s", json_string)

    with open(path, "w", encoding="utf-8") as file:
        file.write(json_string)
        logger.info("custom log config saved path: %s", path)


def create_logging_config(path: str):
    """process create custom logging config"""

    process = multiprocessing.Process(
        target=logging_config,
        args=(path,),
    )
    process.start()

    # Wait for the child process to finish
    process.join()

    if process.exitcode is not None and process.exitcode != 0:
        raise RuntimeError(
            f"Custom logging config process exited with code '{process.exitcode}'"
        )


def preprocess_run() -> str:
    """preprocess function"""

    envs = get_env_variables(
        [
            {"name": "LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT", "required": True},
            {"name": "LLMDBENCH_VLLM_STANDALONE_MODEL", "required": True},
            {"name": "LLMDBENCH_VLLM_TENSORIZER_URI", "required": True},
            {"name": "VLLM_LOGGING_CONFIG_PATH", "required": False},
        ]
    )

    load_format = envs[0].strip().lower()
    model = envs[1].strip()
    tensorizer_uri = envs[2]
    logging_config_path = envs[3]

    if logging_config_path is not None and logging_config_path != "":
        # create custom configuration and save to this path
        create_logging_config(logging_config_path.strip())

    if load_format == "tensorizer":
        # first serialize model in order to run tokenizer library later
        try:
            logger.info(
                "Start model %s serialization for tokenizer library to %s",
                model,
                tensorizer_uri,
            )
            serialize_model(model, tensorizer_uri)
            logger.info("Model %s serialized to %s", model, tensorizer_uri)
        finally:
            logger.info("End model %s serialization", model)


if __name__ == "__main__":
    try:
        logger.info("Start preprocess run")
        preprocess_run()
    except Exception:
        logger.exception("Error running preprocess")
    finally:
        logger.info("End preprocess run")
