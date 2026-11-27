"""AdbDevice, with subprocess and the PATH lookup patched."""

import subprocess
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from android_pilot.device import ADB_TIMEOUT_SECONDS, AdbDevice, DeviceError

ADB = "/usr/bin/adb"
TWO_DEVICES = "List of devices attached\ndevice123\tdevice\ndevice456\tdevice\n"
NO_DEVICES = "List of devices attached\n\n"


def result(stdout: str | bytes = "", stderr: str = "", returncode=0):
    out = stdout.encode() if isinstance(stdout, str) else stdout
    return subprocess.CompletedProcess([ADB], returncode, out, stderr.encode())


def listing(*lines: str) -> subprocess.CompletedProcess:
    return result("List of devices attached\n" + "".join(f"{line}\n" for line in lines))


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


def connected(serial: str | None = None) -> AdbDevice:
    """A device that has already found device123 (or serial)."""
    device = AdbDevice(serial=serial)
    with adb(result(TWO_DEVICES)):
        device.connect()
    return device


def commands(run) -> list[str]:
    return [call.args[0][-1] for call in run.call_args_list]


def executable(tmp_path, mode=0o755) -> str:
    binary = tmp_path / "adb"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(mode)
    return str(binary)


class TestFindingTheDevice:
    @pytest.mark.parametrize("serial", [None, ""])
    def test_defaults_to_the_first_device(self, serial):
        with adb(result(TWO_DEVICES)):
            assert AdbDevice(serial=serial).connect() == (ADB, "device123")

    def test_specific_device(self):
        with adb(result(TWO_DEVICES)):
            assert AdbDevice(serial="device456").connect() == (ADB, "device456")

    def test_only_ready_devices_count(self):
        devices = listing("device456\toffline", "device787\tunauthorized", "device123\tdevice", "")
        with adb(devices):
            assert AdbDevice().connect()[1] == "device123"

    def test_no_devices(self):
        with adb(result(NO_DEVICES)), pytest.raises(DeviceError, match="no devices connected"):
            AdbDevice().connect()

    def test_specific_device_missing_lists_the_ready_ones(self):
        with (
            adb(listing("device123\tdevice", "device787\tunauthorized")),
            pytest.raises(DeviceError, match="device456 not found; connected: device123$"),
        ):
            AdbDevice(serial="device456").connect()

    @pytest.mark.parametrize(
        ("state", "message"),
        [
            (
                "unauthorized",
                'device device123 is unauthorized: unlock the phone and accept the "Allow USB debugging" prompt',
            ),
            (
                "offline",
                "device device123 is offline: reconnect it (cable or wireless debugging), or run `adb kill-server`",
            ),
            ("recovery", "device device123 is recovery: it isn't ready for commands"),
            (
                "no permissions (missing udev rules? user is in the plugdev group); see [http://developer.android.com/tools/device.html]",
                "adb can't access device device123: no permissions (missing udev rules?",
            ),
        ],
    )
    @pytest.mark.parametrize("serial", [None, "device123"])
    def test_a_device_that_isnt_ready_says_how_to_fix_it(self, state, message, serial):
        with adb(listing(f"device123\t{state}")), pytest.raises(DeviceError) as error:
            AdbDevice(serial=serial).connect()
        assert str(error.value).startswith(message)

    def test_tolerates_daemon_startup_lines_crlf_and_spaces_in_states(self):
        output = (
            "* daemon not running; starting now at tcp:5037\r\n"
            "* daemon started successfully\r\n"
            "List of devices attached\r\n"
            "device456\tno permissions (missing udev rules?); see [http://developer.android.com/tools/device.html]\r\n"
            "device123\tdevice  \r\n"
            "\r\n"
        )
        with adb(result(output)):
            assert AdbDevice().connect()[1] == "device123"
        with adb(result(output)), pytest.raises(DeviceError, match="adb can't access device device456: no permissions"):
            AdbDevice(serial="device456").connect()

    def test_announces_the_device_on_stderr(self, capsys):
        connected()
        assert capsys.readouterr().err == f"android-pilot: using device device123 via {ADB}\n"


class TestFindingAdb:
    def test_uses_the_adb_found_on_path(self):
        with adb(result(TWO_DEVICES)) as run:
            AdbDevice().connect()
        assert run.call_args.args[0] == [ADB, "devices"]

    def test_no_adb_on_path_fails_before_running_anything(self):
        with adb(which=None) as run, pytest.raises(DeviceError, match="not on PATH"):
            AdbDevice().shell("ls")
        run.assert_not_called()

    def test_adb_installed_later_is_found_without_a_restart(self):
        device = AdbDevice()
        with adb(which=None), pytest.raises(DeviceError, match="not on PATH"):
            device.shell("ls")
        with adb(result(TWO_DEVICES), result("ok")):
            assert device.shell("ls") == "ok"

    def test_adb_is_looked_up_once(self):
        device = connected()
        with adb(which="/somewhere/else/adb") as run:
            device.shell("ls")
        assert run.call_args.args[0][0] == ADB

    def test_explicit_adb_is_used_without_searching_path(self, tmp_path):
        binary = executable(tmp_path)
        with (
            patch("android_pilot.device.shutil.which", side_effect=AssertionError("must not search PATH")),
            patch("android_pilot.device.subprocess.run", return_value=result(TWO_DEVICES)) as run,
        ):
            assert AdbDevice(binary).connect() == (binary, "device123")
        assert run.call_args.args[0] == [binary, "devices"]

    def test_a_relative_explicit_adb_is_pinned_as_an_absolute_path(self, tmp_path, monkeypatch):
        """subprocess searches PATH for a bare "adb", which would let PATH swap the binary."""
        monkeypatch.chdir(tmp_path)
        executable(tmp_path)
        with patch("android_pilot.device.subprocess.run", return_value=result(TWO_DEVICES)) as run:
            assert AdbDevice("adb").connect()[0] == str(tmp_path / "adb")
        assert run.call_args.args[0][0] == str(tmp_path / "adb")

    def test_a_bare_name_from_path_is_pinned_as_an_absolute_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # which() returns "adb" when PATH has an empty entry
        with adb(result(TWO_DEVICES), which="adb"):
            assert AdbDevice().connect()[0] == str(tmp_path / "adb")

    def test_adb_that_disappears_while_being_checked(self):
        with (
            patch("android_pilot.device.shutil.which", return_value=ADB),
            patch("android_pilot.device.os.stat", side_effect=FileNotFoundError),
            pytest.raises(DeviceError, match=f"adb not found at {ADB}"),
        ):
            AdbDevice().connect()

    def test_explicit_adb_must_exist(self, tmp_path):
        with pytest.raises(DeviceError, match="adb not found at"):
            AdbDevice(str(tmp_path / "missing")).connect()

    def test_explicit_adb_that_cannot_run(self, tmp_path):
        with pytest.raises(DeviceError, match="can't run adb at .*: Permission denied"):
            AdbDevice(executable(tmp_path, 0o644)).connect()

    def test_warns_when_the_adb_on_path_is_world_writable(self, tmp_path):
        with (
            patch("android_pilot.device.shutil.which", return_value=executable(tmp_path, 0o777)),
            patch("android_pilot.device.subprocess.run", return_value=result(TWO_DEVICES)),
            pytest.warns(RuntimeWarning, match="world-writable"),
        ):
            AdbDevice().connect()


class TestConnectingLazily:
    def test_nothing_runs_until_the_device_is_used(self):
        with adb() as run:
            AdbDevice()
        run.assert_not_called()

    def test_first_command_finds_the_device_then_runs(self):
        with adb(result(TWO_DEVICES), result("ok")) as run:
            assert AdbDevice().shell("ls") == "ok"
        assert [call.args[0] for call in run.call_args_list] == [
            [ADB, "devices"],
            [ADB, "-s", "device123", "shell", "ls"],
        ]

    def test_fails_until_a_device_appears(self):
        device = AdbDevice()
        with adb(result(NO_DEVICES)), pytest.raises(DeviceError, match="no devices connected"):
            device.shell("ls")
        with adb(result(TWO_DEVICES), result("ok")):
            assert device.shell("ls") == "ok"

    def test_looks_for_the_device_only_once_while_it_works(self):
        device = connected()
        with adb() as run:
            device.shell("ls")
            device.tap(1, 2)
        assert commands(run) == ["ls", "input tap 1 2"]

    def test_a_command_that_fails_on_a_working_device_keeps_the_device(self):
        device = connected()
        with adb(result(stderr="boom", returncode=1), result(TWO_DEVICES), result("ok")) as run:
            with pytest.raises(DeviceError, match="^boom$"):
                device.shell("false")
            assert device.shell("ls") == "ok"
        assert [call.args[0][1] for call in run.call_args_list] == ["-s", "devices", "-s"]  # no second lookup

    def test_a_failed_command_is_not_retried(self):
        device = connected()
        with adb(result(stderr="boom", returncode=1), result(TWO_DEVICES)) as run, pytest.raises(DeviceError):
            device.tap(1, 2)
        assert commands(run) == ["input tap 1 2", "devices"]

    def test_a_device_that_went_away_is_reported_plainly(self):
        device = connected()
        with (
            adb(result(stderr="adb: device 'device123' not found", returncode=1), result(NO_DEVICES)),
            pytest.raises(DeviceError, match="^no devices connected") as error,
        ):
            device.shell("ls")
        assert "not found" in str(error.value.__cause__)

    def test_a_device_that_went_offline_says_how_to_fix_it(self):
        device = connected()
        with (
            adb(result(stderr="error: device offline", returncode=1), listing("device123\toffline")),
            pytest.raises(DeviceError, match="^device device123 is offline: reconnect it"),
        ):
            device.shell("ls")

    def test_after_the_device_went_away_the_next_command_looks_again(self):
        device = connected()
        rejoined = listing("192.168.1.10:38817\tdevice")
        with adb(result(stderr="not found", returncode=1), rejoined, rejoined, result("ok")) as run:
            with pytest.raises(DeviceError, match="device device123 disconnected; the next call will use 192"):
                device.shell("ls")
            assert device.shell("ls") == "ok"
        assert run.call_args.args[0] == [ADB, "-s", "192.168.1.10:38817", "shell", "ls"]

    def test_a_pinned_device_is_never_swapped(self):
        device = connected("device456")
        others = listing("device123\tdevice")
        with adb(result(stderr="not found", returncode=1), others, others) as run:
            with pytest.raises(DeviceError, match="device456 not found; connected: device123"):
                device.shell("ls")
            with pytest.raises(DeviceError, match="device456 not found; connected: device123"):
                device.shell("ls")
        assert len(run.call_args_list) == 3  # the second call stopped at `adb devices`

    def test_when_adb_itself_fails_too_its_first_error_is_kept(self):
        device = connected()
        with (
            adb(result(stderr="daemon died", returncode=1), result(stderr="cannot connect", returncode=1)),
            pytest.raises(DeviceError, match="^daemon died$"),
        ):
            device.shell("ls")
        assert device.serial is None  # looked up again next time


class TestRunningAdb:
    def test_shell_runs_on_the_device_and_normalises_output(self):
        device = connected()
        with adb(result("one\r\ntwo\r\n")) as run:
            assert device.shell("ls") == "one\ntwo"
        assert run.call_args.args[0] == [ADB, "-s", "device123", "shell", "ls"]

    def test_commands_get_no_stdin(self):
        """The server's stdin is the MCP pipe; device commands must not read it."""
        device = connected()
        with adb() as run:
            device.shell("cat")
        assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL

    def test_failure_reports_stderr(self):
        device = connected()
        with adb(result(stderr="boom", returncode=1), result(TWO_DEVICES)), pytest.raises(DeviceError, match="^boom$"):
            device.shell("ls")

    def test_hanging_command_times_out(self):
        device = connected()
        with (
            patch(
                "android_pilot.device.subprocess.run",
                side_effect=subprocess.TimeoutExpired(ADB, ADB_TIMEOUT_SECONDS),
            ),
            pytest.raises(DeviceError, match="timed out"),
        ):
            device.shell("sleep 999")

    def test_adb_that_vanished_after_it_was_found(self):
        device = connected()
        with (
            patch("android_pilot.device.subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(DeviceError, match=f"adb not found at {ADB}"),
        ):
            device.shell("ls")


class TestDeviceCommands:
    def test_ui_dump_dumps_reads_and_cleans_up_in_one_call(self):
        device = connected()
        with adb(result("<xml/>")) as run:
            assert device.ui_dump() == "<xml/>"
        assert commands(run) == [
            "uiautomator dump /sdcard/window_dump.xml > /dev/null && "
            "cat /sdcard/window_dump.xml && rm -f /sdcard/window_dump.xml"
        ]

    def test_screenshot_comes_through_exec_out_unaltered(self):
        device = connected()
        with adb(result(b"\x89PNG\r\n")) as run:
            assert device.screenshot() == b"\x89PNG\r\n"
        assert run.call_args.args[0] == [ADB, "-s", "device123", "exec-out", "screencap -p"]

    def test_input_commands(self):
        device = connected()
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
        with adb() as run, pytest.raises(ValueError, match="printable ASCII"):
            AdbDevice().type_text("café")
        run.assert_not_called()

    def test_key_rejects_anything_outside_the_list(self):
        with adb() as run, pytest.raises(ValueError, match="unsupported key"):
            AdbDevice().key("POWER; reboot")  # ty: ignore[invalid-argument-type]
        run.assert_not_called()
