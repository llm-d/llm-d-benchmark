import pytest
import argparse

from pathlib import Path
from unittest.mock import MagicMock, patch

from llmdbenchmark.result_store.store import StoreManager, StoreNotFound
from llmdbenchmark.result_store.config import ConfigManager
from llmdbenchmark.result_store.workspace import WorkspaceManager
from llmdbenchmark.interface import results
from llmdbenchmark.result_store.client.gcs import GCSClient, parse_gcs_uri

def test_store_initialization(tmp_path):
    store_dir, created = StoreManager.init_store(target_dir=tmp_path)
    assert created is True
    assert store_dir.name == ".result_store"
    assert (store_dir / "config.json").exists()
    assert (store_dir / "staged.json").exists()
    assert (tmp_path / "workspaces").exists()
    
    root = StoreManager.find_store_root(start_path=tmp_path)
    assert root == tmp_path
    
    config_manager = ConfigManager(config_path=store_dir / "config.json")
    assert config_manager.get_remote("prod") == "gs://llm-d-benchmarks"
    assert config_manager.get_remote("staging") == "gs://llm-d-benchmarks-staging"
    config_manager.add_remote("test", "gs://test-bucket/prefix")
    assert config_manager.get_remote("test") == "gs://test-bucket/prefix"

    config_manager.remove_remote("test")
    with pytest.raises(ValueError):
        config_manager.get_remote("test")
        
def test_store_not_found(tmp_path):
    with pytest.raises(StoreNotFound):
        StoreManager.find_store_root(start_path=tmp_path)

def test_workspace_manager_add_rm(tmp_path):
    staged_file = tmp_path / "staged.json"
    workspace_manager = WorkspaceManager(staged_path=staged_file)
    assert len(workspace_manager.list_staged()) == 0
    
    mock_run = tmp_path / "my_run"
    mock_run.mkdir()
    
    workspace_manager.add_workspace(str(mock_run))
    staged = workspace_manager.list_staged()
    assert len(staged) == 1
    assert staged[0]["status"] == "staged"
    
    workspace_manager.remove_workspace(str(mock_run))
    assert len(workspace_manager.list_staged()) == 0

def test_workspace_manager_overrides(tmp_path):
    staged_file = tmp_path / "staged.json"
    workspace_manager = WorkspaceManager(staged_path=staged_file)
    
    mock_run = tmp_path / "my_run"
    mock_run.mkdir()
    
    overrides = {"hardware": "l4-x1"}
    workspace_manager.add_workspace(str(mock_run), overrides=overrides)
    
    staged = workspace_manager.list_staged()
    assert len(staged) == 1
    assert staged[0]["hardware"] == "l4-x1"

class DummyLogger:
    def log_info(self, msg): pass
    def log_error(self, msg): pass
    def log_warning(self, msg): pass
    def log_debug(self, msg): pass
    def log_plain(self, msg, emoji=None):
        print(msg)

class ResultsStatusTest:
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
        staged_data = [{"status": "staged", "path": "/tmp/workspace/results/experiment1", "run_uid": "123456789", "scenario": "dummy_scenario", "model": "dummy_model", "hardware": "dummy_hardware"}]
        with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=staged_data):
            with patch("pathlib.Path.exists", return_value=False):
                results.execute(args, DummyLogger())
                # Verify print was called at least once indicating staged changes
                assert any("Changes to be pushed" in str(call) for call in mock_print.mock_calls)

    @patch("builtins.print")
    @patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
    def test_status_untracked_only(self, mock_root, mock_print):
        args = argparse.Namespace(results_command="status")
        untracked_data = {"run_uid": "987654321", "scenario": "dummy_scenario", "model": "dummy_model", "hardware": "dummy_hardware"}
        with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=[]):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.iterdir", return_value=[Path("/tmp/workspace")]):
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

def test_parse_gcs_uri():
    bucket, prefix = parse_gcs_uri("gs://my-bucket/some/path")
    assert bucket == "my-bucket"
    assert prefix == "some/path"
    
    bucket, prefix = parse_gcs_uri("gs://my-bucket")
    assert bucket == "my-bucket"
    assert prefix == ""

@patch("llmdbenchmark.result_store.client.gcs.storage.Client")
def test_gcs_client_push(mock_storage, tmp_path):
    mock_bucket = MagicMock()
    mock_storage.return_value.bucket.return_value = mock_bucket
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    
    report_file = tmp_path / "benchmark_report_v0.2_dummy.yaml"
    report_file.touch()
    
    client = GCSClient()
    full_uri = "gs://bucket/default/test/test/test/123"
    
    uploaded_count = client.push(full_uri, str(tmp_path))
    assert uploaded_count == 1
    mock_blob.upload_from_filename.assert_called_with(str(report_file))

def test_cli_results_push():
    args = argparse.Namespace(results_command="push", remote="prod", path=None, group="default")
    logger = DummyLogger()
    
    mock_client = MagicMock()
    mock_client.exists.return_value = False
    mock_client.push.return_value = "gs://dest"
    
    with patch("llmdbenchmark.result_store.commands.push.get_storage_client", return_value=mock_client):
        with patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp")):
            with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.list_staged", return_value=[{"status": "staged", "path": "/tmp/dummy", "run_uid": "123"}]):
                with patch("llmdbenchmark.result_store.workspace.WorkspaceManager.remove_workspace"):
                    results.execute(args, logger)

@patch("llmdbenchmark.result_store.store.StoreManager.find_store_root", return_value=Path("/tmp"))
def test_cli_results_pull(mock_root):
    args = argparse.Namespace(results_command="pull", remote="prod", run_uid="123", dest="/tmp/dest")
    logger = DummyLogger()
    
    mock_client = MagicMock()
    mock_client.ls.return_value = ["default/scenario/model/hardware/123/report_v0.2.yaml"]
    mock_client.pull.return_value = ("/tmp/dest/123", 1)
    
    with patch("llmdbenchmark.result_store.commands.pull.get_storage_client", return_value=mock_client):
        results.execute(args, logger)

def test_cli_push_already_exists_abort():
    args = argparse.Namespace(results_command="push", remote="prod", path=None, group="default")
    logger = DummyLogger()
    
    mock_client = MagicMock()
    mock_client.exists.return_value = True
    
    with patch("llmdbenchmark.result_store.commands.push.get_storage_client", return_value=mock_client):
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
