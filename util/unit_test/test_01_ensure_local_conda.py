#!/usr/bin/env python3

"""
Unit tests for 01_ensure_local_conda.py
Tests the Python conversion using native Python implementation.
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
sys.modules['requests'] = MagicMock()

# Import the module under test
sys.path.insert(0, str(setup_dir))
sys.path.append(str(setup_dir / "steps"))
import importlib.util

# Load the Python module dynamically
spec = importlib.util.spec_from_file_location(
    "ensure_local_conda_py", 
    setup_dir / "steps" / "01_ensure_local_conda.py"
)
module_under_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module_under_test)


class TestEnsureLocalConda(unittest.TestCase):
    """Test cases for the 01_ensure_local_conda.py module"""
    
    def setUp(self):
        """Set up test environment"""
        
        # Mock announce function
        self.announce_calls = []
        
        def mock_announce(message):
            print(f"[TEST ANNOUNCE] {message}")
            self.announce_calls.append(message)
        
        module_under_test.announce = mock_announce
        
        # Mock external dependencies
        self.platform_mock = MagicMock()
        self.subprocess_mock = MagicMock()
        self.shutil_mock = MagicMock()
        self.requests_mock = MagicMock()
        
        # Set up mocks
        module_under_test.platform = self.platform_mock
        module_under_test.subprocess = self.subprocess_mock
        module_under_test.shutil = self.shutil_mock
        module_under_test.requests = self.requests_mock
    
    def test_get_platform_info_macos(self):
        """Test platform detection for macOS"""
        self.platform_mock.system.return_value = 'Darwin'
        self.platform_mock.machine.return_value = 'arm64'
        
        result = module_under_test.get_platform_info()
        
        expected = {
            'system': 'darwin',
            'machine': 'arm64',
            'is_mac': True,
            'is_linux': False
        }
        self.assertEqual(result, expected)
    
    def test_get_platform_info_linux(self):
        """Test platform detection for Linux"""
        self.platform_mock.system.return_value = 'Linux'
        self.platform_mock.machine.return_value = 'x86_64'
        
        result = module_under_test.get_platform_info()
        
        expected = {
            'system': 'linux',
            'machine': 'x86_64',
            'is_mac': False,
            'is_linux': True
        }
        self.assertEqual(result, expected)
    
    def test_is_conda_available_true(self):
        """Test conda availability check when conda exists"""
        self.shutil_mock.which.return_value = '/opt/conda/bin/conda'
        
        result = module_under_test.is_conda_available()
        
        self.assertTrue(result)
        self.shutil_mock.which.assert_called_once_with('conda')
    
    def test_is_conda_available_false(self):
        """Test conda availability check when conda doesn't exist"""
        self.shutil_mock.which.return_value = None
        
        result = module_under_test.is_conda_available()
        
        self.assertFalse(result)
        self.shutil_mock.which.assert_called_once_with('conda')
    
    def test_install_miniforge_macos_dry_run(self):
        """Test macOS miniforge installation in dry run mode"""
        
        exit_code, anaconda_path, conda_sh = module_under_test.install_miniforge_macos(
            dry_run=True, verbose=True
        )
        
        # Verify dry run behavior
        self.assertEqual(exit_code, 0)
        self.assertEqual(anaconda_path, 'export PATH="/opt/homebrew/bin/conda:$PATH"')
        self.assertEqual(conda_sh, Path("/opt/homebrew/Caskroom/miniforge/base/etc/profile.d/conda.sh"))
        
        # Verify announcements
        self.assertIn("🛠️ Installing Miniforge for macOS...", self.announce_calls)
        self.assertIn("---> would execute: brew install --cask miniforge", self.announce_calls)
    
    def test_install_miniforge_macos_no_brew(self):
        """Test macOS miniforge installation when brew is not available"""
        self.shutil_mock.which.return_value = None  # No brew
        
        with self.assertRaises(EnvironmentError) as context:
            module_under_test.install_miniforge_macos(dry_run=False, verbose=True)
        
        self.assertIn("Homebrew not found", str(context.exception))
    
    def test_install_miniforge_macos_success(self):
        """Test successful macOS miniforge installation"""
        self.shutil_mock.which.return_value = '/opt/homebrew/bin/brew'
        
        # Mock successful subprocess call
        mock_result = MagicMock()
        mock_result.returncode = 0
        self.subprocess_mock.run.return_value = mock_result
        
        exit_code, anaconda_path, conda_sh = module_under_test.install_miniforge_macos(
            dry_run=False, verbose=True
        )
        
        # Verify success
        self.assertEqual(exit_code, 0)
        
        # Verify subprocess call
        self.subprocess_mock.run.assert_called_once()
        call_args = self.subprocess_mock.run.call_args[0][0]
        self.assertEqual(call_args, ['brew', 'install', '--cask', 'miniforge'])
    
    def test_install_miniforge_linux_dry_run(self):
        """Test Linux miniforge installation in dry run mode"""
        
        # Mock platform info
        with patch.object(module_under_test, 'get_platform_info') as mock_platform:
            mock_platform.return_value = {
                'system': 'linux',
                'machine': 'x86_64',
                'is_mac': False,
                'is_linux': True
            }
            
            exit_code, anaconda_path, conda_sh = module_under_test.install_miniforge_linux(
                dry_run=True, verbose=True
            )
        
        # Verify dry run behavior
        self.assertEqual(exit_code, 0)
        self.assertEqual(anaconda_path, 'export PATH="/opt/miniconda/bin/conda:$PATH"')
        self.assertEqual(conda_sh, Path("/opt/miniconda/etc/profile.d/conda.sh"))
        
        # Verify announcements
        self.assertIn("🛠️ Installing Miniforge for Linux...", self.announce_calls)
        self.assertTrue(any("would download and install" in call for call in self.announce_calls))
    
    def test_check_conda_environment_exists(self):
        """Test conda environment checking when environment exists"""
        
        # Mock successful conda env list output
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "env1\ntest-env\nenv2\n"
        self.subprocess_mock.run.return_value = mock_result
        
        result = module_under_test.check_conda_environment("test-env")
        
        self.assertTrue(result)
        self.subprocess_mock.run.assert_called_once_with(
            ['conda', 'env', 'list'], capture_output=True, text=True, check=True
        )
    
    def test_check_conda_environment_not_exists(self):
        """Test conda environment checking when environment doesn't exist"""
        
        # Mock successful conda env list output without target env
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "env1\nother-env\nenv2\n"
        self.subprocess_mock.run.return_value = mock_result
        
        result = module_under_test.check_conda_environment("test-env")
        
        self.assertFalse(result)
    
    def test_early_exit_when_not_running_locally(self):
        """Test early exit when LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY=0"""
        
        result = module_under_test.ensure_local_conda(
            run_locally=False,
            host_os="mac",
            host_shell="zsh",
            env_name="test-env",
            dry_run=True,
            verbose=True
        )
        
        # Verify early exit
        self.assertEqual(result, 0)
        self.assertTrue(any("skipping local setup" in call for call in self.announce_calls))
    
    def test_main_function_environment_parsing(self):
        """Test the main function's environment variable parsing"""
        
        # Mock environment variables
        test_env = {
            'LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY': '1',
            'LLMDBENCH_CONTROL_DEPLOY_HOST_OS': 'mac',
            'LLMDBENCH_CONTROL_DEPLOY_HOST_SHELL': 'zsh',
            'LLMDBENCH_HARNESS_CONDA_ENV_NAME': 'test-env',
            'LLMDBENCH_CONTROL_DRY_RUN': '1',
            'LLMDBENCH_CONTROL_VERBOSE': '1'
        }
        
        with patch.dict(os.environ, test_env):
            with patch.object(module_under_test, 'ensure_local_conda') as mock_ensure:
                mock_ensure.return_value = 0
                
                result = module_under_test.main()
                
                # Verify the function was called with correct parameters
                mock_ensure.assert_called_once_with(
                    run_locally=True,
                    host_os='mac',
                    host_shell='zsh',
                    env_name='test-env',
                    dry_run=True,
                    verbose=True
                )
                
                self.assertEqual(result, 0)


class TestCondaWorkflows(unittest.TestCase):
    """Test complete conda setup workflows"""
    
    def setUp(self):
        """Set up test environment"""
        
        # Mock announce function
        self.announce_calls = []
        
        def mock_announce(message):
            self.announce_calls.append(message)
        
        module_under_test.announce = mock_announce
    
    def test_macos_workflow_no_conda(self):
        """Test complete macOS workflow when conda is not installed"""
        
        with patch.multiple(
            module_under_test,
            get_platform_info=MagicMock(return_value={'is_mac': True, 'is_linux': False, 'system': 'darwin'}),
            is_conda_available=MagicMock(return_value=False),
            install_miniforge_macos=MagicMock(return_value=(0, 'anaconda_path', Path('/conda.sh'))),
            update_shell_rc_file=MagicMock(return_value=True),
            source_conda_script=MagicMock(return_value=0),
            create_conda_environment=MagicMock(return_value=0)
        ):
            result = module_under_test.ensure_local_conda(
                run_locally=True,
                host_os='mac',
                host_shell='zsh',
                env_name='test-env',
                dry_run=True,
                verbose=True
            )
            
            # Verify success
            self.assertEqual(result, 0)
            
            # Verify workflow calls
            module_under_test.install_miniforge_macos.assert_called_once()
            module_under_test.update_shell_rc_file.assert_called_once()
            module_under_test.source_conda_script.assert_called_once()
            module_under_test.create_conda_environment.assert_called_once()
    
    def test_linux_workflow_no_conda(self):
        """Test complete Linux workflow when conda is not installed"""
        
        with patch.multiple(
            module_under_test,
            get_platform_info=MagicMock(return_value={'is_mac': False, 'is_linux': True, 'system': 'linux'}),
            is_conda_available=MagicMock(return_value=False),
            install_miniforge_linux=MagicMock(return_value=(0, 'anaconda_path', Path('/conda.sh'))),
            update_shell_rc_file=MagicMock(return_value=True),
            source_conda_script=MagicMock(return_value=0),
            create_conda_environment=MagicMock(return_value=0)
        ):
            result = module_under_test.ensure_local_conda(
                run_locally=True,
                host_os='linux',
                host_shell='bash',
                env_name='test-env',
                dry_run=True,
                verbose=True
            )
            
            # Verify success
            self.assertEqual(result, 0)
            
            # Verify workflow calls
            module_under_test.install_miniforge_linux.assert_called_once()
            module_under_test.update_shell_rc_file.assert_called_once()
            module_under_test.source_conda_script.assert_called_once()
            module_under_test.create_conda_environment.assert_called_once()
    
    def test_source_conda_script_dry_run(self):
        """Test that source_conda_script works in dry run mode without file existence check"""
        
        # Mock announce function
        announce_calls = []
        def mock_announce(message):
            announce_calls.append(message)
        module_under_test.announce = mock_announce
        
        # Test dry run mode - should not check file existence
        result = module_under_test.source_conda_script(
            conda_sh=Path("/nonexistent/conda.sh"),
            dry_run=True,
            verbose=True
        )
        
        # Verify success and correct announcement
        self.assertEqual(result, 0)
        self.assertTrue(any("would source" in call for call in announce_calls))


if __name__ == '__main__':
    unittest.main()