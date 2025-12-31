"""
Tests for AdbDeviceManager
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from adbdevicemanager import AdbDeviceManager

# Add the parent directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAdbDeviceManager:
    """Test AdbDeviceManager functionality"""

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbDeviceManager.get_available_devices')
    @patch('adbdevicemanager.AdbClient')
    def test_single_device_auto_selection(self, mock_adb_client, mock_get_devices, mock_check_adb):
        """Test auto-selection when only one device is connected"""
        # Setup mocks
        mock_check_adb.return_value = True
        mock_get_devices.return_value = ["device123"]
        mock_device = MagicMock()
        mock_adb_client.return_value.device.return_value = mock_device

        # Test with device_name=None (auto-selection)
        with patch('builtins.print') as mock_print:
            manager = AdbDeviceManager(device_name=None, exit_on_error=False)

            # Verify the correct device was selected
            mock_adb_client.return_value.device.assert_called_once_with(
                "device123")
            assert manager.device == mock_device

            # Verify auto-selection message was printed
            mock_print.assert_called_with(
                "No device specified, automatically selected: device123")

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbDeviceManager.get_available_devices')
    def test_multiple_devices_no_selection_error(self, mock_get_devices, mock_check_adb):
        """Test error when multiple devices are connected but none specified"""
        # Setup mocks
        mock_check_adb.return_value = True
        mock_get_devices.return_value = ["device123", "device456"]

        # Test with device_name=None and multiple devices
        with pytest.raises(RuntimeError) as exc_info:
            AdbDeviceManager(device_name=None, exit_on_error=False)

        assert "Multiple devices connected" in str(exc_info.value)
        assert "device123" in str(exc_info.value)
        assert "device456" in str(exc_info.value)

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbDeviceManager.get_available_devices')
    @patch('adbdevicemanager.AdbClient')
    def test_specific_device_selection(self, mock_adb_client, mock_get_devices, mock_check_adb):
        """Test selecting a specific device"""
        # Setup mocks
        mock_check_adb.return_value = True
        mock_get_devices.return_value = ["device123", "device456"]
        mock_device = MagicMock()
        mock_adb_client.return_value.device.return_value = mock_device

        # Test with specific device name
        manager = AdbDeviceManager(
            device_name="device456", exit_on_error=False)

        # Verify the correct device was selected
        mock_adb_client.return_value.device.assert_called_once_with(
            "device456")
        assert manager.device == mock_device

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbDeviceManager.get_available_devices')
    def test_device_not_found_error(self, mock_get_devices, mock_check_adb):
        """Test error when specified device is not found"""
        # Setup mocks
        mock_check_adb.return_value = True
        mock_get_devices.return_value = ["device123", "device456"]

        # Test with non-existent device
        with pytest.raises(RuntimeError) as exc_info:
            AdbDeviceManager(device_name="non-existent-device",
                             exit_on_error=False)

        assert "Device non-existent-device not found" in str(exc_info.value)
        assert "Available devices" in str(exc_info.value)

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbDeviceManager.get_available_devices')
    def test_no_devices_connected_error(self, mock_get_devices, mock_check_adb):
        """Test error when no devices are connected"""
        # Setup mocks
        mock_check_adb.return_value = True
        mock_get_devices.return_value = []

        # Test with no devices
        with pytest.raises(RuntimeError) as exc_info:
            AdbDeviceManager(device_name=None, exit_on_error=False)

        assert "No devices connected" in str(exc_info.value)

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    def test_adb_not_installed_error(self, mock_check_adb):
        """Test error when ADB is not installed"""
        # Setup mocks
        mock_check_adb.return_value = False

        # Test with ADB not installed
        with pytest.raises(RuntimeError) as exc_info:
            AdbDeviceManager(device_name=None, exit_on_error=False)

        assert "adb is not installed" in str(exc_info.value)

    @patch('subprocess.run')
    def test_check_adb_installed_success(self, mock_run):
        """Test successful ADB installation check"""
        mock_run.return_value = MagicMock()  # Successful run

        result = AdbDeviceManager.check_adb_installed()

        assert result is True
        mock_run.assert_called_once()

    @patch('subprocess.run')
    def test_check_adb_installed_failure(self, mock_run):
        """Test failed ADB installation check"""
        mock_run.side_effect = FileNotFoundError()  # ADB not found

        result = AdbDeviceManager.check_adb_installed()

        assert result is False

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbClient')
    def test_get_available_devices(self, mock_adb_client, mock_check_adb):
        """Test getting available devices"""
        # Setup mocks
        mock_check_adb.return_value = True

        mock_device1 = MagicMock()
        mock_device1.serial = "device123"
        mock_device2 = MagicMock()
        mock_device2.serial = "device456"

        mock_adb_client.return_value.devices.return_value = [
            mock_device1, mock_device2]
        mock_adb_client.return_value.device.return_value = MagicMock()

        # Create manager with first device to test get_available_devices
        with patch('builtins.print'):
            manager = AdbDeviceManager(device_name="device123", exit_on_error=False)

        devices = manager.get_available_devices()

        assert devices == ["device123", "device456"]

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbDeviceManager.get_available_devices')
    @patch('adbdevicemanager.AdbClient')
    def test_exit_on_error_true(self, mock_adb_client, mock_get_devices, mock_check_adb):
        """Test that exit_on_error=True calls sys.exit"""
        # Setup mocks to trigger error
        mock_check_adb.return_value = True
        mock_get_devices.return_value = []  # No devices

        # Test with exit_on_error=True (default)
        with patch('sys.exit') as mock_exit:
            with patch('builtins.print'):  # Suppress error output
                AdbDeviceManager(device_name=None, exit_on_error=True)

            mock_exit.assert_called_once_with(1)

    @patch('adbdevicemanager.AdbDeviceManager.check_adb_installed')
    @patch('adbdevicemanager.AdbClient')
    def test_custom_adb_host_port(self, mock_adb_client, mock_check_adb):
        """Test that custom ADB host and port are used"""
        # Setup mocks
        mock_check_adb.return_value = True
        mock_device1 = MagicMock()
        mock_device1.serial = "device123"
        mock_adb_client.return_value.devices.return_value = [mock_device1]
        mock_adb_client.return_value.device.return_value = MagicMock()

        # Test with custom host and port
        custom_host = "192.168.1.100"
        custom_port = 5038

        with patch('builtins.print'):
            manager = AdbDeviceManager(
                device_name=None,
                exit_on_error=False,
                adb_host=custom_host,
                adb_port=custom_port
            )

        # Verify custom host and port are stored
        assert manager.adb_host == custom_host
        assert manager.adb_port == custom_port

        # Verify AdbClient was called with custom host and port
        mock_adb_client.assert_called_with(host=custom_host, port=custom_port)
