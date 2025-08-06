#!/usr/bin/env python3

"""
Unit tests for 00_ensure_llm-d-infra.py
Tests the Python conversion to ensure it behaves identically to the bash version.
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
        
        # Mock announce and llmdbench_execute_cmd functions
        self.announce_calls = []
        self.execute_cmd_calls = []
        
        def mock_announce(message):
            self.announce_calls.append(message)
            print(f"[TEST ANNOUNCE] {message}")
        
        def mock_execute_cmd(actual_cmd, dry_run=False, verbose=False):
            self.execute_cmd_calls.append({
                'cmd': actual_cmd,
                'dry_run': dry_run,
                'verbose': verbose
            })
            print(f"[TEST CMD] {actual_cmd} (dry_run={dry_run}, verbose={verbose})")
            return 0  # Simulate success
        
        # Apply mocks
        self.announce_patch = patch.object(module_under_test, 'announce', mock_announce)
        self.execute_cmd_patch = patch.object(module_under_test, 'llmdbench_execute_cmd', mock_execute_cmd)
        
        self.announce_patch.start()
        self.execute_cmd_patch.start()
    
    def tearDown(self):
        """Clean up test environment"""
        self.announce_patch.stop()
        self.execute_cmd_patch.stop()
        
        # Clean up test directory
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_clone_new_repository(self):
        """Test cloning when llm-d-infra directory doesn't exist"""
        # Reset call tracking
        self.announce_calls.clear()
        self.execute_cmd_calls.clear()
        
        # Run the function
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=True,  # Use dry run for testing
            verbose=True
        )
        
        # Verify success
        self.assertEqual(result, 0)
        
        # Verify expected announcements
        self.assertIn("💾 Cloning and setting up llm-d-infra...", self.announce_calls)
        self.assertIn(f'✅ llm-d-infra is present at "{self.test_dir}"', self.announce_calls)
        
        # Verify git clone command was called
        clone_commands = [call for call in self.execute_cmd_calls if 'git clone' in call['cmd']]
        self.assertEqual(len(clone_commands), 1)
        
        clone_cmd = clone_commands[0]
        self.assertIn(self.git_repo, clone_cmd['cmd'])
        self.assertIn(self.git_branch, clone_cmd['cmd'])
        self.assertTrue(clone_cmd['dry_run'])
        self.assertTrue(clone_cmd['verbose'])
    
    def test_update_existing_repository(self):
        """Test updating when llm-d-infra directory already exists"""
        # Create mock llm-d-infra directory
        llm_d_infra_path = Path(self.test_dir) / "llm-d-infra"
        llm_d_infra_path.mkdir(parents=True)
        
        # Reset call tracking
        self.announce_calls.clear()
        self.execute_cmd_calls.clear()
        
        # Run the function
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=True,
            verbose=True
        )
        
        # Verify success
        self.assertEqual(result, 0)
        
        # Verify expected announcements
        self.assertIn("💾 Cloning and setting up llm-d-infra...", self.announce_calls)
        self.assertIn(f'✅ llm-d-infra is present at "{self.test_dir}"', self.announce_calls)
        
        # Verify git update commands were called (checkout + pull)
        update_commands = [call for call in self.execute_cmd_calls if 'git checkout' in call['cmd'] and 'git pull' in call['cmd']]
        self.assertEqual(len(update_commands), 1)
        
        update_cmd = update_commands[0]
        self.assertIn(self.git_branch, update_cmd['cmd'])
        self.assertTrue(update_cmd['dry_run'])
        self.assertTrue(update_cmd['verbose'])
    
    def test_environment_variable_parsing(self):
        """Test the main function's environment variable parsing"""
        # Set up test environment variables
        test_env = {
            'LLMDBENCH_CONTROL_DIR': str(setup_dir),
            'LLMDBENCH_INFRA_DIR': self.test_dir,
            'LLMDBENCH_INFRA_GIT_REPO': self.git_repo,
            'LLMDBENCH_INFRA_GIT_BRANCH': self.git_branch,
            'LLMDBENCH_CONTROL_DRY_RUN': '1',
            'LLMDBENCH_CONTROL_VERBOSE': '1'
        }
        
        with patch.dict(os.environ, test_env):
            # Mock sys.exit to capture return code
            with patch('sys.exit') as mock_exit:
                try:
                    module_under_test.main()
                    # If main doesn't call sys.exit, it means it returned normally
                    actual_return = 0
                except SystemExit as e:
                    actual_return = e.code
                
                # Verify success (either no exception or exit code 0)
                if mock_exit.called:
                    mock_exit.assert_called_with(0)
                else:
                    self.assertEqual(actual_return, 0)
        
        # Verify that the expected functions were called
        self.assertGreater(len(self.announce_calls), 0)
        self.assertGreater(len(self.execute_cmd_calls), 0)
    
    def test_dry_run_mode(self):
        """Test that dry run mode works correctly"""
        # Reset call tracking
        self.announce_calls.clear()
        self.execute_cmd_calls.clear()
        
        # Run with dry_run=True
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=True,
            verbose=False
        )
        
        # Verify success
        self.assertEqual(result, 0)
        
        # Verify all execute_cmd calls have dry_run=True
        for call in self.execute_cmd_calls:
            self.assertTrue(call['dry_run'], f"Command should be dry run: {call['cmd']}")
    
    def test_verbose_mode(self):
        """Test that verbose mode works correctly"""
        # Reset call tracking
        self.announce_calls.clear()
        self.execute_cmd_calls.clear()
        
        # Run with verbose=True
        result = module_under_test.ensure_llm_d_infra(
            infra_dir=self.test_dir,
            git_repo=self.git_repo,
            git_branch=self.git_branch,
            dry_run=True,
            verbose=True
        )
        
        # Verify success
        self.assertEqual(result, 0)
        
        # Verify all execute_cmd calls have verbose=True
        for call in self.execute_cmd_calls:
            self.assertTrue(call['verbose'], f"Command should be verbose: {call['cmd']}")


class TestCommandCompatibility(unittest.TestCase):
    """Test that Python version generates same commands as bash version"""
    
    def setUp(self):
        """Set up for command compatibility testing"""
        self.captured_commands = []
        
        def mock_execute_cmd(actual_cmd, dry_run=False, verbose=False):
            self.captured_commands.append(actual_cmd)
            return 0
        
        self.execute_patch = patch.object(module_under_test, 'llmdbench_execute_cmd', mock_execute_cmd)
        self.announce_patch = patch.object(module_under_test, 'announce')
        
        self.execute_patch.start()
        self.announce_patch.start()
    
    def tearDown(self):
        """Clean up patches"""
        self.execute_patch.stop()
        self.announce_patch.stop()
    
    def test_clone_command_format(self):
        """Test that clone command format matches bash version"""
        test_dir = "/tmp/test"
        git_repo = "https://github.com/llm-d-incubation/llm-d-infra.git"
        git_branch = "main"
        
        module_under_test.ensure_llm_d_infra(
            infra_dir=test_dir,
            git_repo=git_repo,
            git_branch=git_branch,
            dry_run=True,
            verbose=True
        )
        
        # Find clone command
        clone_commands = [cmd for cmd in self.captured_commands if 'git clone' in cmd]
        self.assertEqual(len(clone_commands), 1)
        
        clone_cmd = clone_commands[0]
        
        # Verify command structure matches bash version
        # Expected: cd ${LLMDBENCH_INFRA_DIR}; git clone "${LLMDBENCH_INFRA_GIT_REPO}" -b "${LLMDBENCH_INFRA_GIT_BRANCH}"
        expected_pattern = f'cd {test_dir}; git clone "{git_repo}" -b "{git_branch}"'
        self.assertEqual(clone_cmd, expected_pattern)
    
    def test_update_command_format(self):
        """Test that update command format matches bash version"""
        test_dir = "/tmp/test"
        git_branch = "main"
        
        # Create mock directory to trigger update path
        with tempfile.TemporaryDirectory() as temp_dir:
            llm_d_infra_path = Path(temp_dir) / "llm-d-infra"
            llm_d_infra_path.mkdir()
            
            module_under_test.ensure_llm_d_infra(
                infra_dir=temp_dir,
                git_repo="https://github.com/llm-d-incubation/llm-d-infra.git",
                git_branch=git_branch,
                dry_run=True,
                verbose=True
            )
        
        # Find update command
        update_commands = [cmd for cmd in self.captured_commands if 'git checkout' in cmd and 'git pull' in cmd]
        self.assertEqual(len(update_commands), 1)
        
        update_cmd = update_commands[0]
        
        # Verify command structure matches bash version
        # Expected: git checkout ${LLMDBENCH_INFRA_GIT_BRANCH}; git pull
        expected_pattern = f"cd {llm_d_infra_path}; git checkout {git_branch}; git pull"
        self.assertEqual(update_cmd, expected_pattern)


def run_tests():
    """Run all tests"""
    # Create test loader
    loader = unittest.TestLoader()
    
    # Create test suite
    suite = unittest.TestSuite()
    
    # Add test cases
    suite.addTests(loader.loadTestsFromTestCase(TestEnsureLlmDInfra))
    suite.addTests(loader.loadTestsFromTestCase(TestCommandCompatibility))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Return success/failure
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())