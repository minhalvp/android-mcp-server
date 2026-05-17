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

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f.read()) or {}
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
            print(f"Configured device: {device_name}")
        else:
            print(f"Loaded config from {CONFIG_FILE}")
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
# AdbDeviceManager will handle auto-selection if device_name is None.
# Device acquisition is deferred — the MCP server starts even when no Android
# device is currently attached, and each tool re-attempts device acquisition
# on first call. This prevents a transient adb-disconnect (e.g., cable unplug,
# device sleep, USB hub power cycle) from crashing the server at startup.
mcp = FastMCP("android")
deviceManager: AdbDeviceManager | None = None

try:
    deviceManager = AdbDeviceManager(device_name, exit_on_error=False)
except RuntimeError as e:
    print(
        f"Android device unavailable at startup: {e} "
        f"MCP server will start in deferred-init mode; tools will re-attempt "
        f"device acquisition on first call.",
        file=sys.stderr,
    )


def _get_device_manager() -> AdbDeviceManager:
    """Return the live device manager, re-attempting acquisition if needed."""
    global deviceManager
    if deviceManager is None:
        try:
            deviceManager = AdbDeviceManager(device_name, exit_on_error=False)
        except RuntimeError as e:
            raise RuntimeError(
                f"Android device unavailable: {e} "
                f"Connect a device via `adb` and retry the tool call."
            )
    return deviceManager


@mcp.tool()
def get_packages() -> str:
    """
    Get all installed packages on the device
    Returns:
        str: A list of all installed packages on the device as a string
    """
    result = _get_device_manager().get_packages()
    return result


@mcp.tool()
def execute_adb_shell_command(command: str) -> str:
    """Executes an ADB command and returns the output or an error.
    Args:
        command (str): The ADB shell command to execute
    Returns:
        str: The output of the ADB command
    """
    result = _get_device_manager().execute_adb_shell_command(command)
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
    result = _get_device_manager().get_uilayout()
    return result


@mcp.tool()
def get_screenshot() -> Image:
    """Takes a screenshot of the device and returns it.
    Returns:
        Image: the screenshot
    """
    _get_device_manager().take_screenshot()
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
    result = _get_device_manager().get_package_action_intents(package_name)
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
