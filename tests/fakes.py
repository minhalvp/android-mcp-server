"""A Device for tests: replays UI dumps and records every action."""

from dataclasses import dataclass, field

from android_pilot.device import Device, Key

EMPTY_SCREEN = '<hierarchy rotation="0"><node bounds="[0,0][400,800]"/></hierarchy>'


@dataclass
class FakeDevice:
    """Returns ``screen`` from every ui_dump; set it to change what the device shows."""

    screen: str = EMPTY_SCREEN
    screenshot_png: bytes = b""
    shell_output: str = ""
    actions: list[tuple] = field(default_factory=list)

    def ui_dump(self) -> str:
        return self.screen

    def screenshot(self) -> bytes:
        return self.screenshot_png

    def tap(self, x: int, y: int) -> None:
        self.actions.append(("tap", x, y))

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.actions.append(("long_press", x, y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.actions.append(("swipe", x1, y1, x2, y2))

    def key(self, key: Key) -> None:
        self.actions.append(("key", key))

    def type_text(self, text: str) -> None:
        self.actions.append(("type", text))

    def delete_text(self, count: int) -> None:
        self.actions.append(("delete", count))

    def shell(self, command: str) -> str:
        self.actions.append(("shell", command))
        return self.shell_output


_: type[Device] = FakeDevice
