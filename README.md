# Android MCP Server

An MCP (Model Context Protocol) server that provides programmatic control over
Android devices through ADB (Android Debug Bridge). This server exposes
various Android device management capabilities that can be accessed by MCP
clients like [Claude desktop](https://modelcontextprotocol.io/quickstart/user)
and Code editors
(e.g. [Cursor](https://docs.cursor.com/context/model-context-protocol))

## Features

- 🔧 ADB Command Execution
- 📸 Device Screenshot Capture
- 🎯 UI Layout Analysis
- 📱 Device Package Management

## Prerequisites

- Python 3.x
- ADB (Android Debug Bridge) installed and configured
- Android device or emulator (not tested)

## Installation

1. Clone the repository:

```bash
git clone https://github.com/minhalvp/android-mcp-server.git
cd android-mcp-server
```

2. Install dependencies:
This project uses [uv](https://github.com/astral-sh/uv) for project
management via various methods of
[installation](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv python install 3.11
uv sync
```

## Configuration

The server supports flexible device configuration with multiple usage scenarios.

### Device Selection Modes

**1. Automatic Selection (Recommended for single device)**

- No configuration file needed
- Automatically connects to the only connected device
- Perfect for development with a single test device

**2. Manual Device Selection**

- Use when you have multiple devices connected
- Specify exact device in configuration file

### Configuration File (Optional)

The configuration file (`config.yaml`) is **optional**. If not present, the server will automatically select the device if only one is connected.

#### For Automatic Selection

Simply ensure only one device is connected and run the server - no configuration needed!

#### For Manual Selection

1. Create a configuration file:

```bash
cp config.yaml.example config.yaml
```

2. Edit `config.yaml` and specify your device:

```yaml
device:
  name: "your-device-serial-here" # Device identifier from 'adb devices'
```

**For auto-selection**, you can use any of these methods:

```yaml
device:
  name: null              # Explicit null (recommended)
  # name: ""              # Empty string  
  # name:                 # Or leave empty/comment out
```

### Finding Your Device Serial

To find your device identifier, run:

```bash
adb devices
```

Example output:

```
List of devices attached
13b22d7f        device
emulator-5554   device
```

Use the first column value (e.g., `13b22d7f` or `emulator-5554`) as the device name.

### Usage Scenarios

| Scenario | Configuration Required | Behavior |
|----------|----------------------|----------|
| Single device connected | None | ✅ Auto-connects to the device |
| Multiple devices, want specific one | `config.yaml` with `device.name` | ✅ Connects to specified device |
| Multiple devices, no config | None | ❌ Shows error with available devices |
| No devices connected | N/A | ❌ Shows "no devices" error |

**Note**: If you have multiple devices connected and don't specify which one to use, the server will show an error message listing all available devices.

## Usage

An MCP client is needed to use this server. The Claude Desktop app is an example
of an MCP client. To use this server with Claude Desktop:

1. Locate your Claude Desktop configuration file:

   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

2. Add the Android MCP server configuration to the `mcpServers` section:

```json
{
  "mcpServers": {
    "android": {
      "command": "path/to/uv",
      "args": ["--directory", "path/to/android-mcp-server", "run", "server.py"]
    }
  }
}
```

Replace:

- `path/to/uv` with the actual path to your `uv` executable
- `path/to/android-mcp-server` with the absolute path to where you cloned this
repository

<https://github.com/user-attachments/assets/c45bbc17-f698-43e7-85b4-f1b39b8326a8>

### Available Tools

The server exposes the following tools:

```python
def get_packages() -> str:
    """
    Get all installed packages on the device.
    Returns:
        str: A list of all installed packages on the device as a string
    """
```

```python
def execute_adb_command(command: str) -> str:
    """
    Executes an ADB command and returns the output.
    Args:
        command (str): The ADB command to execute
    Returns:
        str: The output of the ADB command
    """
```

```python
def get_uilayout() -> str:
    """
    Retrieves information about clickable elements in the current UI.
    Returns a formatted string containing details about each clickable element,
    including their text, content description, bounds, and center coordinates.

    Returns:
        str: A formatted list of clickable elements with their properties
    """
```

```python
def get_screenshot() -> Image:
    """
    Takes a screenshot of the device and returns it.
    Returns:
        Image: the screenshot
    """
```

```python
def get_screenshot_ocr(search_string: str, confidence_threshold: float = 60.0) -> list[dict]:
    """
    Search a screenshot for text and return the locations of it.
    Args:
        search_string: Text string to search for in the screenshot
        confidence_threshold: Minimum OCR confidence threshold 0-100 (default: 60)
    Returns:
        List of dictionaries containing text matches with coordinates
    """
```

```python
def get_screenshot_text(confidence_threshold: float = 30.0) -> list[dict]:
    """
    Get all text from a screenshot using OCR.
    Args:
        confidence_threshold: Minimum OCR confidence threshold 0-100 (default: 30)
    Returns:
        List of dictionaries containing all detected text with coordinates
    """
```

```python
def get_package_action_intents(package_name: str) -> list[str]:
    """
    Get all non-data actions from Activity Resolver Table for a package
    Args:
        package_name (str): The name of the package to get actions for
    Returns:
        list[str]: A list of all non-data actions from the Activity Resolver
        Table for the package
    """
```

## Contributing

Contributions are welcome!

## Acknowledgments

- Built with
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction)
