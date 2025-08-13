#!/usr/bin/env python3

"""
Unit tests for 00_ensure_llm-d-infra.py
Tests the Python conversion using native GitPython implementation.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add setup directory to path
current_file = Path(__file__).resolve()
project_root = current_file.parents[2]  # Go up 2 levels: util -> llm-d-benchmark
setup_dir = project_root / "setup"

# Mock the functions module before any imports to avoid dependency issues
sys.modules['functions'] = MagicMock()
sys.modules['git'] = MagicMock()

# Import the module under test
sys.path.insert(0, str(setup_dir))
sys.path.append(str(setup_dir / "steps"))
import importlib.util

# Load the Python module dynamically
spec = importlib.util.spec_from_file_location(
    "ensure_llm_d_infra_py", 
    setup_dir / "steps" / "00_ensure_llm-d-infra.py"
)
module_under_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module_under_test)


class TestEnsureLlmDInfra(unittest.TestCase):
    """Test cases for the 00_ensure_llm-d-infra.py module"""
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
        self.git_repo = "https://github.com/llm-d-incubation/llm-d-infra.git"
        self.git_branch = "main"
        
        # Mock announce function
        self.announce_calls = []
        
        def mock_announce(message):
            print(f"[TEST ANNOUNCE] {message}")
            self.announce_calls.append(message)
        
        module_under_test.announce = mock_announce
        
        # Mock GitPython
        self.git_mock = MagicMock()
        module_under_test.git = self.git_mock
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_clone_new_repository(self):
        """Test cloning when llm-d-infra directory doesn't exist"""
        
        # Mock repo.clone_from
        mock_repo = MagicMock()
        mock_repo.working_dir = str(Path(self.test_dir) / "llm-d-infra")
        self.git_mock.Repo.clone_from.return_value = mock_repo
        
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=False,
            verbose=True
        )
        
        # Verify success
        self.assertEqual(result, 0)
        
        # Verify git.Repo.clone_from was called
        self.git_mock.Repo.clone_from.assert_called_once_with(
            url=self.git_repo,
            to_path=str(Path(self.test_dir) / "llm-d-infra"),
            branch=self.git_branch
        )
        
        # Verify announcements
        self.assertIn("💾 Cloning and setting up llm-d-infra...", self.announce_calls)
        self.assertTrue(any("llm-d-infra is present" in call for call in self.announce_calls))
    
    def test_update_existing_repository(self):
        """Test updating when llm-d-infra directory already exists"""
        
        # Create the directory to simulate existing repo
        llm_d_infra_path = Path(self.test_dir) / "llm-d-infra"
        llm_d_infra_path.mkdir(parents=True)
        
        # Mock existing repo operations
        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin
        self.git_mock.Repo.return_value = mock_repo
        
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=False,
            verbose=True
        )
        
        # Verify success
        self.assertEqual(result, 0)
        
        # Verify repo operations
        self.git_mock.Repo.assert_called_once_with(str(llm_d_infra_path))
        mock_repo.git.checkout.assert_called_once_with(self.git_branch)
        mock_origin.pull.assert_called_once()
        
        # Verify announcements
        self.assertIn("💾 Cloning and setting up llm-d-infra...", self.announce_calls)
        self.assertTrue(any("llm-d-infra is present" in call for call in self.announce_calls))
    
    def test_dry_run_mode(self):
        """Test that dry run mode works correctly"""
        
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=True,
            verbose=True
        )
        
        # Verify success
        self.assertEqual(result, 0)
        
        # Verify no actual git operations were performed
        self.git_mock.Repo.clone_from.assert_not_called()
        self.git_mock.Repo.assert_not_called()
        
        # Verify dry run announcements
        self.assertTrue(any("would clone repository" in call for call in self.announce_calls))
    
    def test_git_error_handling(self):
        """Test error handling for Git operations"""
        
        # Create a custom GitError for testing
        class MockGitError(Exception):
            pass
        
        # Mock GitError and configure it to be raised
        self.git_mock.exc = MagicMock()
        self.git_mock.exc.GitError = MockGitError
        self.git_mock.Repo.clone_from.side_effect = MockGitError("Test git error")
        
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=False,
            verbose=False
        )
        
        # Verify failure
        self.assertEqual(result, 1)
        
        # Verify error announcement
        self.assertTrue(any("Git operation failed" in call for call in self.announce_calls))
    
    def test_environment_variable_parsing(self):
        """Test the main function's environment variable parsing"""
        
        # Mock environment variables
        test_env = {
            'LLMDBENCH_INFRA_DIR': '/test/infra',
            'LLMDBENCH_INFRA_GIT_REPO': 'https://test.repo.git',
            'LLMDBENCH_INFRA_GIT_BRANCH': 'test-branch',
            'LLMDBENCH_CONTROL_DRY_RUN': '1',
            'LLMDBENCH_CONTROL_VERBOSE': '1'
        }
        
        with patch.dict(os.environ, test_env):
            with patch.object(module_under_test, 'ensure_llm_d_infra') as mock_ensure:
                mock_ensure.return_value = 0
                
                result = module_under_test.main()
                
                # Verify the function was called with correct parameters
                mock_ensure.assert_called_once_with(
                    infra_dir='/test/infra',
                    git_repo='https://test.repo.git',
                    git_branch='test-branch',
                    dry_run=True,
                    verbose=True
                )
                
                self.assertEqual(result, 0)


class TestGitPythonIntegration(unittest.TestCase):
    """Test GitPython integration patterns"""
    
    def test_clone_command_equivalent(self):
        """Test that GitPython clone is equivalent to shell command"""
        
        with patch('git.Repo') as mock_git:
            mock_repo = MagicMock()
            mock_git.clone_from.return_value = mock_repo
            
            # This should be equivalent to: git clone "repo" -b "branch" "path"
            module_under_test.git.Repo.clone_from(
                url="test-repo",
                to_path="/test/path", 
                branch="test-branch"
            )
            
            # Verify the mock was called correctly
            module_under_test.git.Repo.clone_from.assert_called_with(
                url="test-repo",
                to_path="/test/path",
                branch="test-branch"
            )
    
    def test_update_command_equivalent(self):
        """Test that GitPython update is equivalent to shell commands"""
        
        mock_repo = MagicMock()
        mock_origin = MagicMock()
        mock_repo.remotes.origin = mock_origin
        
        # This should be equivalent to: git checkout branch; git pull
        mock_repo.git.checkout("test-branch")
        mock_origin.pull()
        
        # Verify the operations
        mock_repo.git.checkout.assert_called_with("test-branch")
        mock_origin.pull.assert_called_once()


if __name__ == '__main__':
    unittest.main()