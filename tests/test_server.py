"""The tools, called through an in-memory MCP client against a FakeDevice."""

import asyncio
import base64
import io
from unittest.mock import patch

import pytest
from mcp import Client
from mcp.types import ImageContent
from PIL import Image as PILImage

from android_pilot.device import AdbDevice, DeviceError
from android_pilot.server import create_server, main

from .fakes import EMPTY_SCREEN, FakeDevice

SCREEN = """
<hierarchy rotation="0">
  <node index="0" bounds="[0,0][400,800]">
    <node index="0" clickable="true" enabled="true" text="Login" class="android.widget.Button"
          bounds="[0,100][200,150]"/>
    <node index="1" clickable="true" enabled="true" text="abc" class="android.widget.EditText"
          bounds="[0,200][200,250]"/>
    <node index="2" scrollable="true" enabled="true" class="android.widget.ListView" bounds="[0,300][400,700]"/>
  </node>
</hierarchy>
"""


def call(device: FakeDevice, *calls: tuple[str, dict]):
    """Run tool calls in order on one server and return the last result."""

    async def run():
        async with Client(create_server(device)) as client:
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
            return result

    return asyncio.run(run())


def text(result) -> str:
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


def after_snapshot(*calls: tuple[str, dict], screen: str = SCREEN) -> tuple[str, FakeDevice]:
    device = FakeDevice(screen=screen)
    result = call(device, ("snapshot", {}), *calls)
    assert result.is_error is False, text(result)
    return text(result), device


def test_tools_and_instructions_are_advertised():
    async def run():
        async with Client(create_server(FakeDevice())) as client:
            return client.instructions, (await client.list_tools()).tools

    instructions, tools = asyncio.run(run())

    assert {tool.name for tool in tools} == {"snapshot", "screenshot", "tap", "swipe", "type", "key", "shell"}
    assert all(tool.output_schema is None for tool in tools)
    assert instructions is not None and "then_snapshot" in instructions


class TestSnapshot:
    def test_lists_elements_with_refs(self):
        assert text(call(FakeDevice(screen=SCREEN), ("snapshot", {}))).splitlines()[:2] == [
            "screen 400x800",
            'e1 button "Login"',
        ]

    def test_include_offscreen_is_passed_through(self):
        offscreen = SCREEN.replace("[0,100][200,150]", "[0,900][200,950]")
        assert '"Login" [off-screen]' in text(
            call(FakeDevice(screen=offscreen), ("snapshot", {"include_offscreen": True}))
        )

    def test_results_are_not_duplicated_as_structured_content(self):
        assert call(FakeDevice(screen=SCREEN), ("snapshot", {})).structured_content is None


class TestTap:
    def test_by_ref_taps_the_element_center(self):
        output, device = after_snapshot(("tap", {"ref": "e1"}))
        assert output == 'Tapped e1 button "Login"'
        assert device.actions == [("tap", 100, 125)]

    def test_coordinates_are_screenshot_pixels(self):
        device = FakeDevice()
        assert text(call(device, ("tap", {"x": 30, "y": 60}))) == "Tapped (30, 60)"
        assert device.actions == [("tap", 100, 200)]

    def test_hold_long_presses(self):
        output, device = after_snapshot(("tap", {"ref": "e1", "hold": True}))
        assert output.startswith("Long-pressed e1")
        assert device.actions == [("long_press", 100, 125)]

    def test_then_snapshot_returns_the_new_screen(self):
        output, _ = after_snapshot(("tap", {"ref": "e1", "then_snapshot": True}))
        assert output.splitlines()[:4] == ['Tapped e1 button "Login"', "", "screen 400x800", 'e1 button "Login"']

    @pytest.mark.parametrize(
        ("calls", "message"),
        [
            ([("tap", {"x": 5})], "both x and y"),
            ([("tap", {"ref": "e1"})], "unknown ref"),
            ([("snapshot", {}), ("tap", {"ref": "e9"})], "unknown ref"),
        ],
    )
    def test_refuses_without_acting(self, calls, message):
        device = FakeDevice(screen=SCREEN)
        result = call(device, *calls)
        assert result.is_error is True
        assert message in text(result)
        assert device.actions == []

    def test_refuses_when_the_screen_changed_since_the_snapshot(self):
        device = FakeDevice(screen=SCREEN)

        async def run():
            async with Client(create_server(device)) as client:
                await client.call_tool("snapshot", {})
                device.screen = EMPTY_SCREEN
                return await client.call_tool("tap", {"ref": "e1"})

        result = asyncio.run(run())
        assert result.is_error is True
        assert "call snapshot again" in text(result)
        assert device.actions == []


class TestSwipe:
    def test_without_ref_crosses_the_middle_of_the_screen(self):
        device = FakeDevice()
        assert text(call(device, ("swipe", {"direction": "up"}))) == "Swiped up"
        assert device.actions == [("swipe", 200, 640, 200, 160)]

    @pytest.mark.parametrize(
        ("direction", "points"),
        [
            ("up", (200, 620, 200, 380)),
            ("down", (200, 380, 200, 620)),
            ("left", (320, 500, 80, 500)),
            ("right", (80, 500, 320, 500)),
        ],
    )
    def test_with_ref_runs_across_the_element(self, direction, points):
        output, device = after_snapshot(("swipe", {"direction": direction, "ref": "e3"}))
        assert output == f'Swiped {direction} across e3 list "ListView"'
        assert device.actions == [("swipe", *points)]


class TestType:
    def test_into_a_ref_focuses_it_first(self):
        _, device = after_snapshot(("type", {"text": "hi", "ref": "e2"}))
        assert device.actions == [("tap", 100, 225), ("type", "hi")]

    def test_clear_and_submit(self):
        output, device = after_snapshot(("type", {"text": "hi", "ref": "e2", "clear": True, "submit": True}))
        assert output == "Typed and submitted"
        assert device.actions == [("tap", 100, 225), ("delete", 3), ("type", "hi"), ("key", "ENTER")]

    def test_without_ref_types_into_the_focused_field(self):
        device = FakeDevice()
        call(device, ("type", {"text": "hi"}))
        assert device.actions == [("type", "hi")]

    def test_clear_needs_a_ref(self):
        device = FakeDevice()
        assert call(device, ("type", {"text": "hi", "clear": True})).is_error is True
        assert device.actions == []

    def test_works_again_after_the_field_text_changed(self):
        device = FakeDevice(screen=SCREEN)

        async def run():
            async with Client(create_server(device)) as client:
                await client.call_tool("snapshot", {})
                device.screen = SCREEN.replace('text="abc"', 'text="abcde"')
                return await client.call_tool("type", {"text": "x", "ref": "e2", "clear": True})

        assert asyncio.run(run()).is_error is False
        assert ("delete", 5) in device.actions


class TestKeyAndShell:
    def test_key(self):
        device = FakeDevice()
        assert text(call(device, ("key", {"key": "BACK"}))) == "Pressed BACK"
        assert device.actions == [("key", "BACK")]

    def test_unknown_keys_are_rejected_before_reaching_the_device(self):
        device = FakeDevice()
        assert call(device, ("key", {"key": "POWER"})).is_error is True
        assert device.actions == []

    @pytest.mark.parametrize("command", ["ls", "adb shell ls"])
    def test_shell_accepts_pasted_adb_invocations(self, command):
        device = FakeDevice(shell_output="output")
        assert text(call(device, ("shell", {"command": command}))) == "output"
        assert device.actions == [("shell", "ls")]


def test_device_errors_reach_the_agent():
    class Unplugged(FakeDevice):
        def ui_dump(self) -> str:
            raise DeviceError("no devices connected; check the cable and that USB debugging is allowed")

    result = call(Unplugged(), ("snapshot", {}))
    assert result.is_error is True
    assert text(result).endswith(": no devices connected; check the cable and that USB debugging is allowed")


class TestMain:
    def run_main(self, *argv: str):
        with (
            patch("sys.argv", ["android-pilot", *argv]),
            patch("android_pilot.server.create_server") as create,
        ):
            main()
        return create

    def test_starts_without_a_device(self, capsys):
        with patch.object(AdbDevice, "connect", side_effect=DeviceError("no devices connected")):
            create = self.run_main()
        create.return_value.run.assert_called_once_with(transport="stdio")
        assert "no devices connected; tools will keep looking when called" in capsys.readouterr().err

    def test_starts_without_adb(self):
        with patch.object(AdbDevice, "connect", side_effect=DeviceError("adb is not on PATH")):
            self.run_main().return_value.run.assert_called_once()

    def test_passes_the_options_to_the_device(self, tmp_path):
        binary = tmp_path / "adb"
        binary.touch(0o755)
        with patch.object(AdbDevice, "connect") as connect:
            create = self.run_main("--device", "emulator-5554", "--adb-path", str(binary))
        [device] = create.call_args.args
        connect.assert_called_once()
        assert (device._pinned_adb, device._pinned_serial) == (str(binary), "emulator-5554")

    @pytest.mark.parametrize(
        ("name", "mode", "message"),
        [
            ("missing", None, "adb not found at"),
            ("adb", 0o644, "is not executable"),
            ("dir", "dir", "adb not found at"),
        ],
    )
    def test_a_wrong_adb_path_is_fatal(self, tmp_path, capsys, name, mode, message):
        path = tmp_path / name
        if mode == "dir":
            path.mkdir()
        elif mode is not None:
            path.touch(mode)
        with patch.object(AdbDevice, "connect") as connect, pytest.raises(SystemExit) as exit:
            self.run_main("--adb-path", str(path))
        assert exit.value.code == 2
        assert message in capsys.readouterr().err
        connect.assert_not_called()

    def test_a_wrong_adb_path_env_var_is_fatal_too(self, tmp_path, capsys):
        with patch.dict("os.environ", {"ADB_PATH": str(tmp_path / "missing")}), pytest.raises(SystemExit):
            self.run_main()
        assert "adb not found at" in capsys.readouterr().err


def test_screenshot_is_downscaled():
    source = io.BytesIO()
    PILImage.new("RGB", (1000, 2000), "red").save(source, "PNG")

    [image] = call(FakeDevice(screenshot_png=source.getvalue()), ("screenshot", {})).content

    assert isinstance(image, ImageContent)
    with PILImage.open(io.BytesIO(base64.b64decode(image.data))) as img:
        assert (img.format, img.size) == ("PNG", (300, 600))
