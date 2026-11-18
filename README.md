# ardour-lua-agent

Give an AI agent a second, independent way to drive [Ardour](https://ardour.org):
**arbitrary Lua executed inside the running application**, over a file queue with
an OSC fast path.

This is a companion to Ardour's own experimental MCP server, not a replacement.
The native MCP is never modified; this layer only adds operations it does not
expose.

```
 agent (DSH, Claude, any CLI)          Ardour 9 process
 ────────────────────────             ───────────────────────────────────
 ardour_agent.py op track_delete ──►  ~/.cache/ardour-agent/request.lua
                                          │  timer hook (100 ms)
                                          ▼
                                     agent_core.lua → ardour_ops.lua
                                          │  Session / Route / AutomationList
                                          ▼
 ◄── ~/.cache/ardour-agent/response.txt  JSON result
```

## What it adds over the native MCP

| Operation | Native MCP | This layer |
|---|---|---|
| Delete a track or bus | — | `track_delete` |
| Gain/mute/solo automation | — | `automation_add`, `automation_clear` |
| Route inventory with gain/mute/solo | partial | `tracks_list` |
| Arbitrary Lua (experiments, discovery) | — | `ardour_agent.py run file.lua` |
| Rename track, plugin add/remove, fader, mute, solo, markers, regions | ✔ | not duplicated here |

## Install

Requirements: Ardour 9 (tested on the Fedora `ardour9` 9.8.0 package), Python 3.8+
(stdlib only).

```bash
# 1. Ardour must be closed: it rewrites ui_scripts on exit.
./install.sh

# 2. Start Ardour, open a session.
# 3. Enable the surfaces ONCE per session:
#    Window → Preferences → Control Surfaces
#      ☑ Open Sound Control (OSC)
#      ☑ MCP HTTP Server (Experimental)      (only if you also want the native MCP)
#    Then Ctrl+S, so the choice is stored in the session file.

# 4. Verify
./bin/ardour_agent.py doctor
./bin/ardour_agent.py ping
```

`install.sh` writes only user-owned files, backs up anything it replaces, and
never touches the Ardour binary, its MCP module or any system file:

| Path | Purpose |
|---|---|
| `~/.config/ardour9/ui_scripts` | timer hook binding (`<ActionHooks>`) + optional action slot |
| `~/.config/ardour9/scripts/ardour_agent_bridge.lua` | OSC fast-path EditorAction |
| `~/.local/share/ardour-agent/agent_core.lua` | request processor |
| `~/.local/share/ardour-agent/ardour_ops.lua` | operation registry |
| `~/.cache/ardour-agent/` | request/response queue |

## Use

```bash
./bin/ardour_agent.py ping
./bin/ardour_agent.py ops
./bin/ardour_agent.py op tracks_list
./bin/ardour_agent.py op track_delete '{"name":"Drums","dry_run":true}'
./bin/ardour_agent.py op track_delete '{"name":"Drums"}'
./bin/ardour_agent.py op automation_add \
    '{"name":"Vox","param":"gain","points":[{"when":0,"value":1.0},{"when":96000,"value":0.4}]}'
./bin/ardour_agent.py run my_script.lua      # arbitrary Lua, `return <value>`
echo 'return Session:name()' | ./bin/ardour_agent.py run -
```

Every op returns JSON. Destructive ops accept `dry_run: true`.

From Python:

```python
import sys; sys.path.insert(0, "bin")
from ardour_agent import run_lua, op_source
print(run_lua(op_source("track_delete", {"name": "Drums"})))
```

### Writing Lua

```lua
-- runs inside Ardour; `return` crosses the boundary as JSON
local out = {}
for r in Session:get_routes ():iter () do
  out[#out + 1] = { name = r:name (), gain = r:gain_control ():get_value () }
end
return out
```

## How triggering works (and why there are two paths)

Ardour exposes no Lua-execution endpoint. Its only in-process scripting triggers
are EditorActions (reachable over OSC) and **event hooks**, both described in the
upstream Ardour source and in the `ardour-claude-plugin` project this repo's
approach is adapted from.

* **Timer hook (default, reliable).** `ui_scripts` binds a `LuaHook` to
  `LuaTimerS`/`LuaTimerDS`. Every 100 ms it checks the queue and, when a request
  exists, runs it. Latency ≈ 100 ms, zero setup beyond a restart.
* **OSC action (fast path, optional).** The same core is also exposed as an
  EditorAction, reachable with `/access_action LuaAction/script-<slot>`, which
  costs ~1 ms instead of ~100 ms.

> **Verified limitation, Fedora ardour9 9.8.0:** the OSC fast path is *inert*.
> `BasicUI::AccessAction` is defined twice in this build — `nm -D` shows it in
> both `ardour-9.8.0` and `libardourcp.so` — so `libardour_osc.so` emits on a
> signal instance the Editor never connected to. Direct OSC commands
> (`/transport_play`, `/add_marker`, …) still work; `/access_action` does nothing.
> The timer hook runs inside the GUI process and is unaffected, which is why it
> is the default.

## What the Lua API exposes (Ardour 9.8, verified)

Probed live with `ardour_agent.py run`, not copied from old docs.

**Available:** `Session` (`name`, `path`, `sample_rate`, `get_routes`, `get_tracks`,
`playlists`, `remove_route`, `remove_routes`, `new_audio_track`, `new_midi_track`,
`locations`, `save_state`), `Route` (`name`, `set_name`, `gain_control`,
`mute_control`, `solo_control`, `rec_enable_control`, `add_processor`,
`remove_processor`, `nth_plugin`, `is_master`, `is_hidden`), `Controllable`
(`get_value`, `set_value`, `alist`), `AutomationList` (`add(timepos_t, value)`,
`clear(from, to)`, `set_interpolation`), `Editor` (`undo`, `redo`, `get_selection`),
`Temporal` (`timepos_t`, `TempoMap`, `Tempo`, `Meter`), `ARDOUR.LuaAPI`,
`LuaDialog`, `PBD`.

**Not exposed (confirmed absent, so this layer cannot do it):** playlist access
from a track (`Track:playlist()` is nil; `Session:playlists():by_name()` returns a
null pointer), audio import (`Session:import_files` is nil), region lists,
`Session:tempo_map`, and reading automation back (`AutomationList:events()`
returns an opaque userdata). Use the native MCP for the region operations it
covers, and the GUI for audio import.

## Troubleshooting

`./bin/ardour_agent.py doctor` checks install, process, port and round-trip.

| Symptom | Cause / fix |
|---|---|
| `no response from the Ardour bridge` | Ardour not running, hook not loaded, or sandboxed Lua. Restart Ardour after `install.sh`. |
| hook never fires | `signals` mask is version-specific (Ardour 9.x: 54 bits, timers at index 50/51). Re-run `install.sh`; for another version set `ARDOUR_AGENT_HOOK_SIGNALS`. |
| `cannot load ardour_ops.lua` | `~/.local/share/ardour-agent/` missing or stale — re-run `install.sh`. |
| OSC port 3819 not listening | Enable OSC in Preferences → Control Surfaces, then **save the session** (surfaces are per-session state). |
| `sandbox` errors mentioning `io` | Turn off Preferences → Lua → *Sandbox all Lua scripts*. |
| `/access_action` does nothing | Known 9.8 build issue above; the timer hook is the working path. |

## Attribution

The Lua-over-file-queue + OSC action approach, and parts of the documentation
structure, are adapted from [DuaneNielsen/ardour-claude-plugin](https://github.com/DuaneNielsen/ardour-claude-plugin)
(MIT). See `ATTRIBUTION.md`. The hook trigger, the shared core, the operation
registry and all verification against Ardour 9.8 are this project's work.

## License

MIT — see `LICENSE`.
