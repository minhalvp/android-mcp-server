import os
import sys

import yaml
from mcp.server.fastmcp import FastMCP, Image

from adbdevicemanager import AdbDeviceManager

CONFIG_FILE = "config.yaml"
CONFIG_FILE_EXAMPLE = "config.yaml.example"

# Load config (make config file optional)
config = {}
device_name = None
adb_host = "127.0.0.1"
adb_port = 5037

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f.read()) or {}

        # Load ADB server configuration
        adb_config = config.get("adb", {})
        if adb_config:
            adb_host = adb_config.get("host", adb_host)
            adb_port = adb_config.get("port", adb_port)

        device_config = config.get("device", {})
        configured_device_name = device_config.get(
            "name") if device_config else None

        # Support multiple ways to specify auto-selection:
        # 1. name: null (None in Python)
        # 2. name: "" (empty string)
        # 3. name field completely missing
        if configured_device_name and configured_device_name.strip():
            device_name = configured_device_name.strip()
            print(f"Loaded config from {CONFIG_FILE}")
            print(f"ADB server: {adb_host}:{adb_port}")
            print(f"Configured device: {device_name}")
        else:
            print(f"Loaded config from {CONFIG_FILE}")
            print(f"ADB server: {adb_host}:{adb_port}")
            print(
                "No device specified in config, will auto-select if only one device connected")
    except Exception as e:
        print(f"Error loading config file {CONFIG_FILE}: {e}", file=sys.stderr)
        print(
            f"Please check the format of your config file or recreate it from {CONFIG_FILE_EXAMPLE}", file=sys.stderr)
        sys.exit(1)
else:
    print(
        f"Config file {CONFIG_FILE} not found, using auto-selection for device")

# Initialize MCP and device manager
# AdbDeviceManager will handle auto-selection if device_name is None
mcp = FastMCP("android")
deviceManager = AdbDeviceManager(device_name, adb_host=adb_host, adb_port=adb_port)


@mcp.tool()
def get_packages() -> str:
    """
    Get all installed packages on the device
    Returns:
        str: A list of all installed packages on the device as a string
    """
    result = deviceManager.get_packages()
    return result


@mcp.tool()
def execute_adb_shell_command(command: str) -> str:
    """Executes an ADB command and returns the output or an error.
    Args:
        command (str): The ADB shell command to execute
    Returns:
        str: The output of the ADB command
    """
    result = deviceManager.execute_adb_shell_command(command)
    return result


@mcp.tool()
def get_uilayout() -> str:
    """
    Retrieves information about clickable elements in the current UI.
    Returns a formatted string containing details about each clickable element,
    including its text, content description, bounds, and center coordinates.

    Returns:
        str: A formatted list of clickable elements with their properties
    """
    result = deviceManager.get_uilayout()
    return result


@mcp.tool()
def get_screenshot() -> Image:
    """Takes a screenshot of the device and returns it.
    Returns:
        Image: the screenshot
    """
    deviceManager.take_screenshot()
    return Image(path="compressed_screenshot.png")


@mcp.tool()
def get_package_action_intents(package_name: str) -> list[str]:
    """
    Get all non-data actions from Activity Resolver Table for a package
    Args:
        package_name (str): The name of the package to get actions for
    Returns:
        list[str]: A list of all non-data actions from the Activity Resolver Table for the package
    """
    result = deviceManager.get_package_action_intents(package_name)
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
