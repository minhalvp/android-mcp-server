"""AdbDevice and connect(), with subprocess and the PATH lookup patched."""

import subprocess
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from android_pilot.device import ADB_TIMEOUT_SECONDS, AdbDevice, DeviceError, connect

ADB = "/usr/bin/adb"
TWO_DEVICES = "List of devices attached\ndevice123\tdevice\ndevice456\tdevice\n"


def result(stdout: str | bytes = "", stderr: str = "", returncode=0):
    out = stdout.encode() if isinstance(stdout, str) else stdout
    return subprocess.CompletedProcess([ADB], returncode, out, stderr.encode())


@contextmanager
def adb(*results, which: str | None = ADB):
    """Yields the subprocess.run mock, which returns one of results per call, then empty successes."""
    queue = list(results)
    with (
        patch("android_pilot.device.shutil.which", return_value=which),
        patch("android_pilot.device.os.stat") as fake_stat,
        patch(
            "android_pilot.device.subprocess.run", side_effect=lambda *a, **k: queue.pop(0) if queue else result()
        ) as run,
    ):
        fake_stat.return_value.st_mode = 0o755
        yield run


def commands(run) -> list[str]:
    return [call.args[0][-1] for call in run.call_args_list]


def executable(tmp_path, mode=0o755) -> str:
    binary = tmp_path / "adb"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(mode)
    return str(binary)


class TestConnect:
    @pytest.mark.parametrize("serial", [None, ""])
    def test_defaults_to_the_first_device(self, serial):
        with adb(result(TWO_DEVICES)):
            assert connect(serial).serial == "device123"

    def test_specific_device(self):
        with adb(result(TWO_DEVICES)):
            assert connect("device456").serial == "device456"

    def test_only_ready_devices_count(self):
        listing = "List of devices attached\ndevice456\toffline\ndevice787\tunauthorized\ndevice123\tdevice\n\n"
        with adb(result(listing)):
            assert connect(None).serial == "device123"
        with adb(result(listing)), pytest.raises(DeviceError, match="device456 not found; connected: device123"):
            connect("device456")

    def test_no_devices(self):
        with adb(result("List of devices attached\n\n")), pytest.raises(DeviceError, match="no devices connected"):
            connect(None)

    def test_uses_the_adb_found_on_path(self):
        with adb(result(TWO_DEVICES)) as run:
            assert connect(None).adb_path == ADB
        assert run.call_args.args[0] == [ADB, "devices"]

    def test_no_adb_on_path_fails_before_running_anything(self):
        with adb(which=None) as run, pytest.raises(DeviceError, match="not on PATH"):
            connect(None)
        run.assert_not_called()

    def test_explicit_adb_is_used_without_searching_path(self, tmp_path):
        binary = executable(tmp_path)
        with (
            patch("android_pilot.device.shutil.which", side_effect=AssertionError("must not search PATH")),
            patch("android_pilot.device.subprocess.run", return_value=result(TWO_DEVICES)) as run,
        ):
            assert connect(None, binary).adb_path == binary
        assert run.call_args.args[0] == [binary, "devices"]

    def test_explicit_adb_must_exist(self, tmp_path):
        with pytest.raises(DeviceError, match="adb not found at"):
            connect(None, str(tmp_path / "missing"))

    def test_warns_when_the_adb_on_path_is_world_writable(self, tmp_path):
        with (
            patch("android_pilot.device.shutil.which", return_value=executable(tmp_path, 0o777)),
            patch("android_pilot.device.subprocess.run", return_value=result(TWO_DEVICES)),
            pytest.warns(RuntimeWarning, match="world-writable"),
        ):
            connect(None)


class TestRunningAdb:
    def test_shell_runs_on_the_device_and_normalises_output(self):
        with adb(result("one\r\ntwo\r\n")) as run:
            assert AdbDevice("device123", ADB).shell("ls") == "one\ntwo"
        assert run.call_args.args[0] == [ADB, "-s", "device123", "shell", "ls"]

    def test_commands_get_no_stdin(self):
        """The server's stdin is the MCP pipe; device commands must not read it."""
        with adb() as run:
            AdbDevice("device123", ADB).shell("cat")
        assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL

    def test_failure_reports_stderr(self):
        with adb(result(stderr="boom", returncode=1)), pytest.raises(DeviceError, match="boom"):
            AdbDevice("device123", ADB).shell("ls")

    def test_hanging_command_times_out(self):
        with (
            patch(
                "android_pilot.device.subprocess.run",
                side_effect=subprocess.TimeoutExpired(ADB, ADB_TIMEOUT_SECONDS),
            ),
            pytest.raises(DeviceError, match="timed out"),
        ):
            AdbDevice("device123", ADB).shell("sleep 999")

    def test_adb_that_vanished_after_startup(self):
        with (
            patch("android_pilot.device.subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(DeviceError, match=f"adb not found at {ADB}"),
        ):
            AdbDevice("device123", ADB).shell("ls")


class TestDeviceCommands:
    def test_ui_dump_dumps_reads_and_cleans_up_in_one_call(self):
        with adb(result("<xml/>")) as run:
            assert AdbDevice("device123", ADB).ui_dump() == "<xml/>"
        assert commands(run) == [
            "uiautomator dump /sdcard/window_dump.xml > /dev/null && "
            "cat /sdcard/window_dump.xml && rm -f /sdcard/window_dump.xml"
        ]

    def test_screenshot_comes_through_exec_out_unaltered(self):
        with adb(result(b"\x89PNG\r\n")) as run:
            assert AdbDevice("device123", ADB).screenshot() == b"\x89PNG\r\n"
        assert run.call_args.args[0] == [ADB, "-s", "device123", "exec-out", "screencap -p"]

    def test_input_commands(self):
        device = AdbDevice("device123", ADB)
        with adb() as run:
            device.tap(10, 20)
            device.long_press(30, 40, 700)
            device.swipe(10, 100, 10, 20, 300)
            device.key("BACK")
            device.type_text("hi there; rm")
            device.delete_text(2)
            device.type_text("")
            device.delete_text(0)
        assert commands(run) == [
            "input tap 10 20",
            "input swipe 30 40 30 40 700",
            "input swipe 10 100 10 20 300",
            "input keyevent KEYCODE_BACK",
            "input text 'hi%sthere;%srm'",
            "input keyevent KEYCODE_MOVE_END KEYCODE_DEL KEYCODE_DEL",
        ]

    def test_type_text_rejects_non_ascii(self):
        with pytest.raises(ValueError, match="printable ASCII"):
            AdbDevice("device123", ADB).type_text("café")

    def test_key_rejects_anything_outside_the_list(self):
        with pytest.raises(ValueError, match="unsupported key"):
            AdbDevice("device123", ADB).key("POWER; reboot")  # ty: ignore[invalid-argument-type]
