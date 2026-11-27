"""The phone, driven through the adb binary. The only module that runs anything."""

import os
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import warnings
from typing import Literal, Protocol, get_args

Key = Literal["BACK", "HOME", "ENTER", "ESCAPE", "APP_SWITCH", "TAB", "DEL"]
_KEYS = frozenset(get_args(Key))

# Long enough for a slow uiautomator dump or screencap, short enough that a
# wedged device or adb server can't hold a tool call open forever.
ADB_TIMEOUT_SECONDS = 15


class DeviceError(Exception):
    """adb is missing or failed, or no device is ready. The message says how to fix it."""


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


class AdbDevice:
    """A phone reached through adb, found on first use so the server can start before it's plugged in.

    adb_path pins the binary; without it, adb is looked up on PATH on first use and
    kept. serial pins the device; without it, the first ready device is used. When
    a command fails because the device went away, the error says so and the next
    command looks again, so a phone that reconnected under a new serial (as
    `adb connect` to a new wireless-debugging port does) is found.
    """

    def __init__(self, adb_path: str | None = None, serial: str | None = None) -> None:
        self._pinned_adb = adb_path
        self._pinned_serial = serial or None
        self.adb_path: str | None = None
        self.serial: str | None = None
        self._lock = threading.Lock()  # tools run on worker threads

    def connect(self) -> tuple[str, str]:
        """The adb binary and the serial of a ready device, looking them up if needed."""
        with self._lock:
            if self.adb_path is None:
                self.adb_path = _resolve_adb(self._pinned_adb)
            if self.serial is None:
                self.serial = _pick_serial(_device_states(self.adb_path), self._pinned_serial)
                print(f"android-pilot: using device {self.serial} via {self.adb_path}", file=sys.stderr)
            return self.adb_path, self.serial

    def _adb(self, *args: str) -> bytes:
        adb, serial = self.connect()
        try:
            return _run(adb, "-s", serial, *args)
        except DeviceError as error:
            # Never retry here: an action could land twice. Just find out whether
            # the device went away, to say so plainly and look again next time.
            try:
                states = _device_states(adb)
            except DeviceError:
                states = None  # adb itself is in trouble; its first error says more
            if states is not None and states.get(serial) == "device":
                raise  # the device is fine; the command itself failed
            with self._lock:
                if self.serial == serial:
                    self.serial = None
            if states is None:
                raise
            raise self._gone(serial, states) from error

    def _gone(self, serial: str, states: dict[str, str]) -> DeviceError:
        """Why serial, which was in use, can't be used now."""
        if serial in states:
            return _not_ready(serial, states[serial])
        try:
            other = _pick_serial(states, self._pinned_serial)
        except DeviceError as e:
            return e
        return DeviceError(f"device {serial} disconnected; the next call will use {other}")

    def ui_dump(self) -> str:
        return self.shell(
            "uiautomator dump /sdcard/window_dump.xml > /dev/null && "
            "cat /sdcard/window_dump.xml && rm -f /sdcard/window_dump.xml"
        )

    def screenshot(self) -> bytes:
        """A full-resolution PNG, captured in memory and never written to disk."""
        return self._adb("exec-out", "screencap -p")

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
        output = self._adb("shell", command).decode(errors="replace")
        return output.replace("\r\n", "\n").rstrip("\n")


# What to do about each state `adb devices` can report other than "device" (ready).
_NOT_READY_HINTS = {
    "unauthorized": 'unlock the phone and accept the "Allow USB debugging" prompt',
    "authorizing": 'unlock the phone and accept the "Allow USB debugging" prompt',
    "offline": "reconnect it (cable or wireless debugging), or run `adb kill-server`",
    "connecting": "wait a moment and retry",
}


def _device_states(adb: str) -> dict[str, str]:
    """Each device `adb devices` lists, and its state ("device" when ready)."""
    states: dict[str, str] = {}
    for line in _run(adb, "devices").decode(errors="replace").splitlines()[1:]:  # skip "List of devices attached"
        name, sep, state = line.strip().partition("\t")
        if sep:
            states[name] = state.strip()
    return states


def _pick_serial(states: dict[str, str], serial: str | None) -> str:
    """The serial of the device to use: this one if given, else the first ready one."""
    ready = [name for name, state in states.items() if state == "device"]
    if serial:
        if serial in ready:
            return serial
        if serial in states:
            raise _not_ready(serial, states[serial])
        raise DeviceError(f"device {serial} not found; connected: {', '.join(ready) or 'none'}")
    if ready:
        return ready[0]
    if states:
        raise _not_ready(*next(iter(states.items())))
    raise DeviceError("no devices connected; check the cable and that USB debugging is allowed")


def _not_ready(serial: str, state: str) -> DeviceError:
    if state.startswith("no permissions"):  # Linux, missing udev rules; adb's message says how to fix it
        return DeviceError(f"adb can't access device {serial}: {state}")
    hint = _NOT_READY_HINTS.get(state, "it isn't ready for commands")
    return DeviceError(f"device {serial} is {state}: {hint}")


def _resolve_adb(explicit: str | None) -> str:
    """Pick the adb binary once, on first use, so a changed PATH can't swap it under a running server.

    adb can shell into every connected device as this user, so an
    attacker-controlled adb on PATH is code execution; ``explicit`` pins a trusted one.
    The result is absolute: subprocess searches PATH again for a bare name like "adb".
    """
    if explicit:
        if not os.path.isfile(explicit):
            raise DeviceError(f"adb not found at {explicit}")
        return os.path.abspath(explicit)
    found = shutil.which("adb")
    if found is None:
        raise DeviceError("adb is not on PATH; install platform-tools or pass --adb-path")
    found = os.path.abspath(found)  # which() returns a bare name for an empty PATH entry
    try:
        mode = os.stat(found).st_mode
    except OSError as exc:  # removed between which() and here
        raise DeviceError(f"adb not found at {found}") from exc
    if mode & stat.S_IWOTH:
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
    except OSError as exc:  # e.g. not executable
        raise DeviceError(f"can't run adb at {adb}: {exc.strerror or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeviceError(f"adb {' '.join(args)} timed out after {ADB_TIMEOUT_SECONDS}s; is the device stuck?") from exc
    if result.returncode != 0:
        raise DeviceError(result.stderr.decode(errors="replace").strip() or f"adb {' '.join(args)} failed")
    return result.stdout
