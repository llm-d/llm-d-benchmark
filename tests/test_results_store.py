import pytest
import argparse

from pathlib import Path
from unittest.mock import MagicMock, patch

from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
from llmdbenchmark.result_store.config import ConfigManager
from llmdbenchmark.result_store.workspace import WorkspaceManager
from llmdbenchmark.interface import results

def test_store_initialization(tmp_path):
    store_dir, created = StoreManager.init_store(target_dir=tmp_path)
    assert created is True
    assert store_dir.name == ".result_store"
    assert (store_dir / "config.json").exists()
    assert (store_dir / "staged.json").exists()
    assert (tmp_path / "workspaces").exists()
    
    root = StoreManager.find_store_root(start_path=tmp_path)
    assert root == tmp_path
    
    cm = ConfigManager(config_path=store_dir / "config.json")
    assert cm.get_remote("prod") == "gs://llm-d-benchmarks"
    assert cm.get_remote("staging") == "gs://llm-d-benchmarks-staging"
    cm.add_remote("test", "gs://test-bucket/prefix")
    assert cm.get_remote("test") == "gs://test-bucket/prefix"

    cm.remove_remote("test")
    with pytest.raises(ValueError):
        cm.get_remote("test")
        
def test_store_not_found(tmp_path):
    with pytest.raises(StoreNotFound):
        StoreManager.find_store_root(start_path=tmp_path)

def test_workspace_manager_add_rm(tmp_path):
    staged_file = tmp_path / "staged.json"
    wm = WorkspaceManager(staged_path=staged_file)
    assert len(wm.list_staged()) == 0
    
    mock_run = tmp_path / "my_run"
    mock_run.mkdir()
    
    wm.add_workspace(str(mock_run))
    staged = wm.list_staged()
    assert len(staged) == 1
    assert staged[0]["status"] == "staged"
    
    wm.remove_workspace(str(mock_run))
    assert len(wm.list_staged()) == 0

def test_workspace_manager_overrides(tmp_path):
    staged_file = tmp_path / "staged.json"
    wm = WorkspaceManager(staged_path=staged_file)
    
    mock_run = tmp_path / "my_run"
    mock_run.mkdir()
    
    overrides = {"hardware": "l4-x1"}
    wm.add_workspace(str(mock_run), overrides=overrides)
    
    staged = wm.list_staged()
    assert len(staged) == 1
    assert staged[0]["hardware"] == "l4-x1"

class DummyLogger:
    def log_info(self, msg): pass
    def log_error(self, msg): pass
    def log_warning(self, msg): pass
    def log_debug(self, msg): pass

class TestCLIStatus:
    """Grouped tests for the results status command covering all sub-paths."""
    
    @patch("builtins.print")
    @patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
    def test_status_empty(self, mock_root, mock_print):
        args = argparse.Namespace(results_command="status")
        with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=[]):
            with patch("pathlib.Path.exists", return_value=False):
                results.execute(args, DummyLogger())
                mock_print.assert_called_with("No benchmark runs found in /tmp/workspaces.")

    @patch("builtins.print")
    @patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
    def test_status_staged_only(self, mock_root, mock_print):
        args = argparse.Namespace(results_command="status")
        staged_data = [{"status": "staged", "path": "/tmp/ws/results/exp1", "run_uid": "123456789", "scenario": "s", "model": "m", "hardware": "h"}]
        with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=staged_data):
            with patch("pathlib.Path.exists", return_value=False):
                results.execute(args, DummyLogger())
                # Verify print was called at least once indicating staged changes
                assert any("Changes to be pushed" in str(call) for call in mock_print.mock_calls)

    @patch("builtins.print")
    @patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
    def test_status_untracked_only(self, mock_root, mock_print):
        args = argparse.Namespace(results_command="status")
        untracked_data = {"run_uid": "987654321", "scenario": "s", "model": "m", "hardware": "h"}
        with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=[]):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.iterdir", return_value=[Path("/tmp/ws")]):
                    with patch("pathlib.Path.is_dir", return_value=True):
                        with patch("llmdbenchmark.result_store.workspace.WorkspaceManager._parse_report", return_value=untracked_data):
                            results.execute(args, DummyLogger())
                            # Verify print was called indicating untracked results
                            assert any("Untracked results" in str(call) for call in mock_print.mock_calls)

@patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
def test_cli_results_add(mock_root, tmp_path):
    args = argparse.Namespace(results_command="add", paths=["dummy_path"])
    logger = DummyLogger()
    with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.add_workspace") as mock_add:
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("llmdbenchmark.result_store.workspace.WorkspaceManager._parse_report", return_value={"scenario": "test", "model": "test", "hardware": "test-x1"}):
                    results.execute(args, logger)
                    mock_add.assert_called()

@patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
def test_cli_results_rm(mock_root):
    args = argparse.Namespace(results_command="rm", paths=["c6bc210e"])
    logger = DummyLogger()
    with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.remove_workspace") as mock_rm:
        with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=[{"run_uid": "c6bc210e", "path": "/tmp/dummy"}]):
            results.execute(args, logger)
            mock_rm.assert_called_with("/tmp/dummy")

@patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
def test_cli_add_interactive_validation_loop(mock_root, tmp_path):
    args = argparse.Namespace(results_command="add", paths=["dummy_path"])
    logger = DummyLogger()
    report_data = {"scenario": "test", "model": "test", "hardware": "missing"}
    
    with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.add_workspace", return_value=True) as mock_add:
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("llmdbenchmark.result_store.workspace.WorkspaceManager._parse_report", return_value=report_data):
                    with patch("sys.stdout.isatty", return_value=True):
                        # User inputs 'l4' (invalid, missing count), then 'l4-x1' (valid)
                        with patch("builtins.input", side_effect=["l4", "l4-x1"]):
                            results.execute(args, logger)
                            # Should have called add_workspace with overrides={"hardware": "l4-x1"}
                            mock_add.assert_called_with("dummy_path", overrides={"hardware": "l4-x1"})

@patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
def test_cli_add_interactive_empty_abort(mock_root, tmp_path):
    args = argparse.Namespace(results_command="add", paths=["dummy_path"])
    logger = DummyLogger()
    report_data = {"scenario": "test", "model": "test", "hardware": "missing"}
    
    with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.add_workspace") as mock_add:
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("llmdbenchmark.result_store.workspace.WorkspaceManager._parse_report", return_value=report_data):
                    with patch("sys.stdout.isatty", return_value=True):
                        with patch("builtins.input", return_value=""):
                            results.execute(args, logger)
                            mock_add.assert_not_called()

from llmdbenchmark.result_store.gcs import GCSClient

def test_gcs_client_parse_uri():
    client = GCSClient()
    bucket, prefix = client._parse_uri("gs://my-bucket/some/path")
    assert bucket == "my-bucket"
    assert prefix == "some/path"
    
    bucket, prefix = client._parse_uri("gs://my-bucket")
    assert bucket == "my-bucket"
    assert prefix == ""

@patch("llmdbenchmark.result_store.gcs.storage.Client")
def test_gcs_client_push(mock_storage, tmp_path):
    mock_bucket = MagicMock()
    mock_storage.return_value.bucket.return_value = mock_bucket
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    
    report_file = tmp_path / "benchmark_report_v0.2_dummy.yaml"
    report_file.touch()
    
    client = GCSClient()
    metadata = {"scenario": "test", "model": "test", "hardware": "test", "run_uid": "123"}
    
    dest = client.push("gs://bucket", str(tmp_path), metadata)
    assert dest == "gs://bucket/default/test/test/test/123"
    mock_blob.upload_from_filename.assert_called_with(str(report_file))

def test_cli_results_push():
    args = argparse.Namespace(results_command="push", remote="prod", path=None, group="default")
    logger = DummyLogger()
    
    with patch("llmdbenchmark.result_store.gcs.GCSClient.exists", return_value=False):
        with patch("llmdbenchmark.result_store.gcs.GCSClient.push", return_value="gs://dest"):
            with patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp")):
                with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=[{"status": "staged", "path": "/tmp/dummy", "run_uid": "123"}]):
                    with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.remove_workspace"):
                        results.execute(args, logger)

@patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
def test_cli_results_pull(mock_root):
    args = argparse.Namespace(results_command="pull", remote="prod", run_uid="123", dest="/tmp/dest")
    logger = DummyLogger()
    
    with patch("llmdbenchmark.result_store.gcs.GCSClient.ls", return_value=[{"run_uid": "123", "path": "gs://dummy"}]):
        with patch("llmdbenchmark.result_store.gcs.GCSClient.pull"):
            results.execute(args, logger)

def test_cli_push_already_exists_abort():
    args = argparse.Namespace(results_command="push", remote="prod", path=None, group="default")
    logger = DummyLogger()
    
    with patch("llmdbenchmark.result_store.gcs.GCSClient.exists", return_value=True):
        with patch("sys.stdout.isatty", return_value=False):
            with patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp")):
                with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=[{"status": "staged", "path": "/tmp/dummy", "run_uid": "123"}]):
                    with pytest.raises(SystemExit):
                        results.execute(args, logger)

def test_cli_add_not_found():
    args = argparse.Namespace(results_command="add", paths=["nonexistent_uid_1234"])
    logger = DummyLogger()
    
    with patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp")):
        with patch("pathlib.Path.exists", return_value=False):
            results.execute(args, logger)

def test_cli_init_fail():
    args = argparse.Namespace(results_command="init")
    logger = DummyLogger()
    
    with patch("llmdbenchmark.result_store.store.StoreManager.init_store", side_effect=Exception("Disk Full")):
        with pytest.raises(SystemExit):
            results.execute(args, logger)
