# Changelog

## 0.2.0 (unreleased)

A rewrite around **element refs**: `snapshot` lists each actionable element
with a short ref (`e1`, `e2`, ...), and the interaction tools act on that ref
instead of pixel coordinates.

### Upgrading from 0.1

- **New name.** The project is now published as `android-pilot`. Replace
  `"run", "server.py"` in your client config with `uvx android-pilot` (see the
  README), or `uv run android-pilot` from a checkout.
- **Device selection.** `config.yaml` is no longer read. Pass
  `--device <serial>` instead. Without it, the first connected device is used.
- **Renamed tools.**
  - `get_uilayout` → `snapshot`, which now returns compact text with one line
    per element and a ref, instead of the old multi-line coordinate listing.
    Pass `include_offscreen=true` to list elements that aren't on screen.
  - `get_screenshot` → `screenshot`
  - `execute_adb_shell_command` → `shell`
- **Removed tools.** `get_packages` and `get_package_action_intents`. Use
  `shell` instead (e.g. `pm list packages`).

### Added

- Tools: `tap` (with `hold=true` for a long press), `swipe`, `type` (with
  `clear` and `submit`) and `key`. Each accepts a ref from `snapshot`; `tap`
  also accepts `x`/`y` in screenshot pixels.
- `then_snapshot=true` on any action returns the resulting screen in the same
  reply.
- Server instructions that explain the snapshot → ref → act workflow once,
  so the tool descriptions stay short.
- Refs are re-checked against the live screen before every action, and the
  server refuses to act if the element has changed or gone. An element that
  only moved (a keyboard opened, a list re-sorted) is still found, unless
  several identical elements now match it.
- `--adb-path` flag and `ADB_PATH` environment variable to pin the adb binary.
- 15-second timeout on every adb call.
- The server starts without a device or adb, instead of exiting before the
  client can connect. Tools report what's missing (no device, unauthorized,
  offline, no permissions, adb not on PATH) with how to fix it, and look again
  on the next call, so plugging the phone in later needs no restart. A device
  that reconnects under a new serial (for example after `adb connect` to a new
  wireless-debugging port) is found again. A wrong `--adb-path` still stops the
  server at startup.

### Changed

- Calls the `adb` binary directly. `pure-python-adb` and `PyYAML` are no longer
  dependencies; `mcp` 2.x is required.
- Screenshots are processed in memory and never written to disk.
- Screenshots are sent as JPEG (quality 80) instead of PNG, roughly a third of
  the size. Model APIs count image tokens from pixel dimensions, not file
  size, so the token cost is unchanged.
- Tool results are plain text only, without a duplicate structured copy.
- Text in fields that Android marks as passwords is left out of snapshots and
  error messages, including the label of a row that contains the field.
  Android drops that mark when the user reveals the password (for example with
  a "Show password" toggle), and from then on the text is shown like any
  other. A password field is labelled by
  its description or resource ID instead.
