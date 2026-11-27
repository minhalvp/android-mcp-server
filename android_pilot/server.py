"""The MCP server: the tools a model uses to drive the phone, and the command that starts it."""

import argparse
import functools
import os
import sys
from collections.abc import Callable
from io import BytesIO
from typing import Literal, ParamSpec, TypeVar, get_args

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image as PILImage

from . import __version__
from .device import AdbDevice, Device, DeviceError, Key
from .screen import EMPTY, FLAGS, Bounds, Element, Point, RefError, Role, Snapshot

P = ParamSpec("P")
R = TypeVar("R")

Direction = Literal["up", "down", "left", "right"]

# Screenshots are sent at this fraction of the device's resolution, and tap's
# x, y are read in the same pixels.
SCREENSHOT_SCALE = 0.3
LONG_PRESS_MS = 700
SWIPE_MS = 300
_SWIPE_SPAN = 0.6  # fraction of the region the finger travels

INSTRUCTIONS = f"""\
Drive an Android device.

Call snapshot to read the screen as text. Each actionable element is one line
with a ref, a role, a label and flags, e.g. `e4 switch "Wi-Fi" [checked]`;
other visible text appears as `text "..."`. Pass refs to tap, swipe and type.
Roles: {", ".join(get_args(Role))}. Flags: {", ".join(FLAGS)}.

Refs come from the latest snapshot. Before acting, the server checks the
element is still there; if the screen changed it refuses and asks for a new
snapshot, so it never taps the wrong thing. Set then_snapshot=true on an action
to get the resulting screen in the same reply instead of calling snapshot again.

Use screenshot only when the text isn't enough (images, maps, games, canvases).
Its pixel coordinates can be passed to tap as x, y.
"""


def create_server(device: Device) -> MCPServer:
    mcp = MCPServer("android-pilot", version=__version__, instructions=INSTRUCTIONS)
    issued = EMPTY

    def read() -> Snapshot:
        return Snapshot.parse(device.ui_dump())

    def observe(include_offscreen: bool = False) -> str:
        nonlocal issued
        issued = read()
        return issued.render(include_offscreen)

    def locate(ref: str) -> tuple[Element, Bounds]:
        return issued.locate(ref, read())

    def done(message: str, then_snapshot: bool) -> str:
        return f"{message}\n\n{observe()}" if then_snapshot else message

    def tool(name: str | None = None):
        def register(fn: Callable[P, R]) -> Callable[P, R]:
            # The SDK hides the message of any exception other than ToolError
            # from the model, so re-raise the failures we expect as ToolError.
            @functools.wraps(fn)
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                try:
                    return fn(*args, **kwargs)
                except (ValueError, RefError, DeviceError) as e:
                    raise ToolError(str(e)) from e

            # Every result is plain text; structured output would send it twice.
            mcp.tool(name=name, structured_output=False)(wrapper)
            return wrapper

        return register

    @tool()
    def snapshot(include_offscreen: bool = False) -> str:
        """Read the current screen as text, one line per element in screen order, and issue fresh refs.

        include_offscreen: also list elements scrolled out of view (browsers have many).
        """
        return observe(include_offscreen)

    @tool()
    def screenshot() -> Image:
        """Look at the screen as an image. Prefer snapshot; use this for visual content it can't describe."""
        with PILImage.open(BytesIO(device.screenshot())) as image:
            size = (int(image.width * SCREENSHOT_SCALE), int(image.height * SCREENSHOT_SCALE))
            out = BytesIO()
            image.resize(size, PILImage.Resampling.LANCZOS).save(out, "PNG", optimize=True)
        return Image(data=out.getvalue(), format="png")

    @tool()
    def tap(
        ref: str | None = None,
        x: int | None = None,
        y: int | None = None,
        hold: bool = False,
        then_snapshot: bool = False,
    ) -> str:
        """Tap an element by ref, or a point (x, y) in screenshot pixels.

        hold: long-press instead, e.g. to open a context menu or start selecting.
        """
        if ref is not None:
            element, bounds = locate(ref)
            point, target = bounds.center, str(element)
        elif x is not None and y is not None:
            point, target = Point(round(x / SCREENSHOT_SCALE), round(y / SCREENSHOT_SCALE)), f"({x}, {y})"
        else:
            raise ValueError("pass a ref from snapshot, or both x and y")
        if hold:
            device.long_press(*point, LONG_PRESS_MS)
        else:
            device.tap(*point)
        return done(f"{'Long-pressed' if hold else 'Tapped'} {target}", then_snapshot)

    @tool()
    def swipe(direction: Direction, ref: str | None = None, then_snapshot: bool = False) -> str:
        """Move a finger across the screen, or across one element such as a list, in direction.

        The content moves with the finger: "up" scrolls down to reveal what is below.
        """
        if ref is not None:
            element, bounds = locate(ref)
            target = f" across {element}"
        else:
            bounds, target = read().screen, ""  # read fresh, in case the screen rotated
        start, end = _swipe_points(direction, bounds)
        device.swipe(*start, *end, SWIPE_MS)
        return done(f"Swiped {direction}{target}", then_snapshot)

    @tool(name="type")
    def type_text(
        text: str,
        ref: str | None = None,
        clear: bool = False,
        submit: bool = False,
        then_snapshot: bool = False,
    ) -> str:
        """Type printable ASCII into a field.

        ref: the field to type into; it is tapped first. Without it, text goes to the focused field.
        clear: delete the field's existing text first (needs ref).
        submit: press Enter afterwards, e.g. to run a search.
        """
        if ref is not None:
            element, bounds = locate(ref)
            device.tap(*bounds.center)
            if clear:
                device.delete_text(len(element.text))
        elif clear:
            raise ValueError("clear needs ref, to know how much text the field holds")
        device.type_text(text)
        if submit:
            device.key("ENTER")
        return done("Typed" + (" and submitted" if submit else ""), then_snapshot)

    @tool()
    def key(key: Key, then_snapshot: bool = False) -> str:
        """Press a system or keyboard key: BACK and HOME to navigate, APP_SWITCH for recent apps."""
        device.key(key)
        return done(f"Pressed {key}", then_snapshot)

    @tool()
    def shell(command: str) -> str:
        """Run a command in the device's shell and return its output, e.g. `am start -n <pkg>/<activity>`,
        `pm list packages`, `dumpsys window | grep mCurrentFocus`. Has full access to the device."""
        return device.shell(command.removeprefix("adb shell "))

    return mcp


def _swipe_points(direction: Direction, bounds: Bounds) -> tuple[Point, Point]:
    (cx, cy), dx, dy = bounds.center, int(bounds.width * _SWIPE_SPAN / 2), int(bounds.height * _SWIPE_SPAN / 2)
    return {
        "up": (Point(cx, cy + dy), Point(cx, cy - dy)),
        "down": (Point(cx, cy - dy), Point(cx, cy + dy)),
        "left": (Point(cx + dx, cy), Point(cx - dx, cy)),
        "right": (Point(cx - dx, cy), Point(cx + dx, cy)),
    }[direction]


def main() -> None:
    parser = argparse.ArgumentParser(prog="android-pilot", description="Let an AI agent drive an Android device")
    parser.add_argument("--device", help="serial from 'adb devices' (default: the first connected device)")
    parser.add_argument(
        "--adb-path",
        default=os.environ.get("ADB_PATH"),
        help="adb binary to use (default: $ADB_PATH, then adb on PATH); set it to pin a trusted binary",
    )
    args = parser.parse_args()
    # A wrong value the user configured is fatal. Anything missing from the
    # environment (adb, the phone) is reported by the tools instead, where the
    # agent can see it, and fixing it doesn't need a restart.
    if args.adb_path:
        if not os.path.isfile(args.adb_path):
            parser.error(f"adb not found at {args.adb_path}")
        if not os.access(args.adb_path, os.X_OK):
            parser.error(f"adb at {args.adb_path} is not executable")
    device = AdbDevice(args.adb_path, args.device)
    try:
        device.connect()  # only to report the device in the client's log; stdout is the MCP transport
    except DeviceError as e:
        print(f"android-pilot: {e}; tools will keep looking when called", file=sys.stderr)
    create_server(device).run(transport="stdio")
