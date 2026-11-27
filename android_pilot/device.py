"""The phone, driven through the adb binary. The only module that runs anything."""

import os
import shlex
import shutil
import stat
import subprocess
import warnings
from typing import Literal, Protocol, get_args

Key = Literal["BACK", "HOME", "ENTER", "ESCAPE", "APP_SWITCH", "TAB", "DEL"]
_KEYS = frozenset(get_args(Key))

# Long enough for a slow uiautomator dump or screencap, short enough that a
# wedged device or adb server can't hold a tool call open forever.
ADB_TIMEOUT_SECONDS = 15


class DeviceError(Exception):
    """adb is missing or failed, or the requested device isn't connected."""


class Device(Protocol):
    """What the server needs from a phone. AdbDevice is the real one; tests use a fake."""

    def ui_dump(self) -> str: ...
    def screenshot(self) -> bytes: ...
    def tap(self, x: int, y: int) -> None: ...
    def long_press(self, x: int, y: int, duration_ms: int) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None: ...
    def key(self, key: Key) -> None: ...
    def type_text(self, text: str) -> None: ...
    def delete_text(self, count: int) -> None: ...
    def shell(self, command: str) -> str: ...


def connect(serial: str | None = None, adb_path: str | None = None) -> "AdbDevice":
    """The device with this serial, or the first connected one."""
    adb = _resolve_adb(adb_path)
    lines = _run(adb, "devices").decode(errors="replace").splitlines()[1:]  # skip "List of devices attached"
    ready = [line.split("\t")[0] for line in lines if line.endswith("\tdevice")]
    if serial:
        if serial not in ready:
            raise DeviceError(f"device {serial} not found; connected: {', '.join(ready) or 'none'}")
        return AdbDevice(serial, adb)
    if not ready:
        raise DeviceError("no devices connected; check the cable and that USB debugging is allowed")
    return AdbDevice(ready[0], adb)


class AdbDevice:
    def __init__(self, serial: str, adb_path: str) -> None:
        self.serial = serial
        self.adb_path = adb_path

    def ui_dump(self) -> str:
        return self.shell(
            "uiautomator dump /sdcard/window_dump.xml > /dev/null && "
            "cat /sdcard/window_dump.xml && rm -f /sdcard/window_dump.xml"
        )

    def screenshot(self) -> bytes:
        """A full-resolution PNG, captured in memory and never written to disk."""
        return _run(self.adb_path, "-s", self.serial, "exec-out", "screencap -p")

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {x} {y}")

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.swipe(x, y, x, y, duration_ms)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

    def key(self, key: Key) -> None:
        if key not in _KEYS:  # it is pasted into a shell command
            raise ValueError(f"unsupported key {key!r}; expected one of {', '.join(sorted(_KEYS))}")
        self.shell(f"input keyevent KEYCODE_{key}")

    def type_text(self, text: str) -> None:
        if any(not 32 <= ord(c) <= 126 for c in text):
            raise ValueError("adb can only type printable ASCII")
        if text:
            self.shell(f"input text {shlex.quote(text.replace(' ', '%s'))}")

    def delete_text(self, count: int) -> None:
        """Delete count characters from the end of the focused field."""
        if count > 0:
            self.shell("input keyevent KEYCODE_MOVE_END" + " KEYCODE_DEL" * count)

    def shell(self, command: str) -> str:
        output = _run(self.adb_path, "-s", self.serial, "shell", command).decode(errors="replace")
        return output.replace("\r\n", "\n").rstrip("\n")


def _resolve_adb(explicit: str | None) -> str:
    """Pick the adb binary once, at startup, so a changed PATH can't swap it under a running server.

    adb can shell into every connected device as this user, so an
    attacker-controlled adb on PATH is code execution; ``explicit`` pins a trusted one.
    """
    if explicit:
        if not os.path.isfile(explicit):
            raise DeviceError(f"adb not found at {explicit}")
        return explicit
    found = shutil.which("adb")
    if found is None:
        raise DeviceError("adb is not on PATH; install platform-tools or pass --adb-path")
    if os.stat(found).st_mode & stat.S_IWOTH:
        warnings.warn(f"{found} is world-writable, so anyone on this machine can replace it", RuntimeWarning, 3)
    return found


def _run(adb: str, *args: str) -> bytes:
    try:
        result = subprocess.run(
            [adb, *args],
            capture_output=True,
            # The server's stdin is the MCP transport, and "adb shell" forwards
            # stdin to the device, where a command like "cat" would eat it.
            stdin=subprocess.DEVNULL,
            timeout=ADB_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise DeviceError(f"adb not found at {adb}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeviceError(f"adb {' '.join(args)} timed out after {ADB_TIMEOUT_SECONDS}s; is the device stuck?") from exc
    if result.returncode != 0:
        raise DeviceError(result.stderr.decode(errors="replace").strip() or f"adb {' '.join(args)} failed")
    return result.stdout
