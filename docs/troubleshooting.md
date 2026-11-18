# Troubleshooting

Start with `./bin/ardour_agent.py doctor`. It checks, in order: bridge files
installed, ops installed, Ardour process, OSC UDP 3819, and a full ping
round-trip. The first `FAIL` is where to look.

## `no response from the Ardour bridge`

The request was written but nothing consumed it.

1. Was Ardour restarted **after** `install.sh`? The hook is read from
   `ui_scripts` at startup only.
2. Is the hook bound? `grep -c "Ardour Agent Timer" ~/.config/ardour9/ui_scripts`
   returns non-zero for the base64 payload even when bound — instead decode it:
   `python3 -c "import base64,xml.etree.ElementTree as ET; t=ET.parse('$HOME/.config/ardour9/ui_scripts'); print(base64.b64decode(t.getroot().find('ActionHooks').find('LuaCallback').text)[:40])"`
3. Is Lua sandboxed? Preferences → Lua → *Sandbox all Lua scripts* must be off
   (`io` is stripped when it is on).

## The hook never fires

The `signals` bitmask is version-specific. For Ardour 9.x there are 54 signals,
`LuaTimerS` at index 50 and `LuaTimerDS` at 51, which `std::bitset::to_string()`
prints at positions 3 and 2 → `signals="0011" + 50 zeros`.

On a different Ardour, pass the mask explicitly:

```bash
ARDOUR_AGENT_HOOK_SIGNALS="$(python3 - <<'PY'
n, taps = int(input("signal count: ")), [int(x) for x in input("indices: ").split()]
print("".join("1" if i in taps else "0" for i in range(n)))
PY
)" ./install.sh
```

## OSC 3819 is not listening

OSC is enabled per **session**, and only saved when you save the session.

1. Window → Preferences → Control Surfaces → tick *Open Sound Control (OSC)*.
2. Ctrl+S (save the session). Reopening a session saved without it re-disables it.
3. `ss -lun | grep 3819` should then show a UNCONN socket.

## `/access_action` silently does nothing

Known Ardour 9.8 packaging issue, confirmed with `nm -D`:

```
ardour-9.8.0            B BasicUI::AccessAction
libardourcp.so          B BasicUI::AccessAction
```

Two instances of the same static signal. `libardour_osc.so` emits on the one it
links; the Editor connected to the other. Direct OSC commands (`/transport_play`,
`/add_marker`, `/goto_start`, strips) still work because they do not cross that
signal. The timer hook is unaffected — it runs inside the GUI process.

## `cannot load ardour_ops.lua` / `unknown op`

`~/.local/share/ardour-agent/ardour_ops.lua` is stale or missing. Re-run
`install.sh` (it copies the current file; the running hook reloads it on the next
tick, no restart needed for ops changes).

## `bad argument #1 to 'add' (timepos_t expected, got number)`

`AutomationList:add` and `clear` take `Temporal.timepos_t`, not plain numbers.

```lua
al:add (Temporal.timepos_t (48000), 0.5)
al:clear (Temporal.timepos_t (0), Temporal.timepos_t (1000000000000))
```

## `shared_ptr is nil`

Usually `Session:playlists():by_name(...)`. Playlist lookup is not usable in 9.8;
region work belongs to the native MCP (`region_*` tools) or the GUI.

## Concurrent calls

There is one request slot. Two agents writing at once will race: the last writer
wins and the earlier caller times out. Serialise callers, or give each its own
`ARDOUR_AGENT_STATE` directory — but note the hook only reads
`~/.cache/ardour-agent` unless the Lua script is edited to match.
