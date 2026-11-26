# android-pilot

<!-- mcp-name: io.github.minhalvp/android-pilot -->

Let an AI assistant use an Android phone or emulator. android-pilot is an
[MCP](https://modelcontextprotocol.io) server that drives your device over
[adb](https://developer.android.com/tools/adb), so any MCP client (Claude Code,
Codex, OpenCode, ...) can read the screen and interact with it.

<!-- TODO: add a demo video/GIF here -->

## Why element refs

Most adb wrappers make the model read a screenshot or XML dump and guess pixel
coordinates. Here, `snapshot` returns compact text in which every actionable
element has a ref (`e1`, `e2`, ...), and the interaction tools take that ref:

```text
snapshot           → e2 switch "Wi-Fi" [unchecked]
tap(ref="e2")      → Tapped e2 switch "Wi-Fi" [unchecked]
```

The model never works out a coordinate, and the server refuses to act if the
screen changed under it. Picking pixel positions is a well-known weak spot: in
[AppAgent](https://arxiv.org/abs/2312.13771) (CHI 2025), GPT-4 completed 2.2%
of Android tasks with raw coordinate actions and 48.9% with element-ID actions.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [adb](https://developer.android.com/tools/releases/platform-tools) on your `PATH`
- An Android device with
  [USB debugging](https://developer.android.com/studio/debug/dev-options#enable)
  enabled, or an emulator

## Quick start

Add this to your MCP client's config (most clients use this format; check
your client's docs for where the file lives):

```json
{
  "mcpServers": {
    "android": {
      "command": "uvx",
      "args": ["android-pilot"]
    }
  }
}
```

Or, in Claude Code:

```bash
claude mcp add android -- uvx android-pilot
```

Then check that adb can see your device:

```bash
adb devices
```

```text
List of devices attached
13b22d7f        device
```

If it says `unauthorized`, unlock the phone and accept the "Allow USB
debugging" prompt. Restart your client and ask something like *"Open Settings
and tell me which Wi-Fi network I'm connected to."*

## Options

Add these after `"android-pilot"` in `args`:

| Option | Default | Purpose |
|--------|---------|---------|
| `--device <serial>` | first device in `adb devices` | Choose a device when several are connected: a serial (`13b22d7f`), emulator (`emulator-5554`) or wireless device (`192.168.1.10:5555`, after `adb connect`) |
| `--adb-path <path>` | `$ADB_PATH`, then `adb` on `PATH` | Pin the adb binary to use |

The server prints the device and adb binary it chose to stderr.

## Tools

| Tool | Purpose |
|------|---------|
| `snapshot(include_offscreen=False)` | The screen as compact text, with a ref for each actionable element |
| `screenshot()` | The screen as an image, at 30% of the device's resolution |
| `tap(ref \| x, y, hold=False)` | Tap (or long-press) an element, or a point in screenshot pixels |
| `swipe(direction, ref=None)` | Swipe `up`, `down`, `left` or `right` across an element or the screen. `up` scrolls down |
| `type(text, ref=None, clear=False, submit=False)` | Type printable ASCII. `ref` focuses the field first, `clear` empties it, `submit` presses Enter |
| `key(key)` | `BACK`, `HOME`, `ENTER`, `ESCAPE`, `APP_SWITCH`, `TAB` or `DEL` |
| `shell(command)` | Run any `adb shell` command and return its output |

Every action also takes `then_snapshot=true`, which returns the resulting
screen in the same reply and saves a round trip. Results are plain text only,
with no duplicate structured copy, and the workflow is explained once in the
server's instructions instead of in every tool description.

<details>
<summary>What <code>snapshot</code> returns</summary>

One line per element, in screen order, with only the state that matters:

```text
screen 1080x2400
text "Network & internet"
e1 switch "Wi-Fi (Wi-Fi is on)" [checked]
e2 item "Internet"
e3 edit "Search settings" [focused]
e4 button "Save" [disabled]
e5 list "recycler view"
```

- **`eN`** is the ref to pass to the interaction tools.
- **Role:** `button`, `edit`, `switch`, `checkbox`, `radio`, `dropdown`, `tab`,
  `list` (scrollable) or `item` (anything else clickable).
- **Label:** the element's text or description, or one built from its children
  or resource ID. Long labels are truncated.
- **Flags** appear only when they differ from the default: `checked` /
  `unchecked`, `focused`, `selected`, `disabled`, `password` (the field's text
  is left out; see below), `scroll`, `long-click` and `off-screen`.
- **`text "…"`** lines are non-interactive text, for context.

Off-screen elements (browsers expose many) are left out unless you pass
`include_offscreen=true`; the output says how many were omitted.

Text in a field that Android marks as a password is left out of snapshots and
error messages; the field is labelled by its description or resource ID
instead. Android drops that mark when a password is revealed (a "Show
password" toggle, for example), and the text is then shown like any other.
Screenshots show whatever is on the screen.

</details>

<details>
<summary>How refs stay safe</summary>

Refs belong to the most recent `snapshot`. Before acting on one, the server
reads the screen again and checks that the element at the same place in the UI
tree still has the same class, resource ID and label. If it does, the server
uses the element's *current* position (clipped to the screen, so a half-visible
element is tapped on its visible part), so small layout shifts don't break the
tap. If it doesn't, the tool does nothing and asks for a new `snapshot`, rather
than tapping whatever now sits at the old position.

</details>

## Security

`shell` gives the client full shell access to your device, and the other tools
can tap, type and read the screen. Only connect clients you trust.

adb itself is a high-trust program: it can shell into every connected device.
Pass `--adb-path` to pin a binary you trust instead of relying on `PATH`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Client can't start the server | Use the full path to `uvx` (`which uvx`) as `command`; GUI clients often don't share your shell's `PATH` |
| `adb is not installed or not in PATH` | Install [platform-tools](https://developer.android.com/tools/releases/platform-tools), or pass `--adb-path /full/path/to/adb` |
| `No devices connected` | Check the cable, enable USB debugging, run `adb devices` |
| Device shows `unauthorized` | Accept the "Allow USB debugging" prompt; if it doesn't appear, run `adb kill-server` and retry |
| `Device … not found` | `--device` must match the first column of `adb devices` exactly |
| `adb … timed out after 30s` | The device or adb server is stuck: replug, or run `adb kill-server` |
| `… changed or is gone; call snapshot again` | The screen changed since the snapshot. Call `snapshot` and retry |
| `… moved and N elements now match it` | The element moved, and several identical ones (e.g. repeated "Details" rows) are now on screen, so the server won't guess. Call `snapshot` and retry |
| `type` fails on non-ASCII | adb text entry supports printable ASCII only |

## Development

```bash
git clone https://github.com/minhalvp/android-mcp-server.git
cd android-mcp-server
uv sync               # installs the project and dev tools (pytest, ruff, ty)
uv run pytest
uv run ruff check .
uv run ty check
```

To try a local checkout in a client, use
`"command": "uv", "args": ["--directory", "/path/to/android-mcp-server", "run", "android-pilot"]`.

```text
android_pilot/
  server.py   the MCP tools, the ref store and the android-pilot command
  screen.py   UI Automator XML → elements with refs → compact text; ref checks
  device.py   the adb binary: the only module that runs anything
```

Changes are listed in [CHANGELOG.md](https://github.com/minhalvp/android-mcp-server/blob/master/CHANGELOG.md).

## License

[Apache 2.0](https://github.com/minhalvp/android-mcp-server/blob/master/LICENSE)
