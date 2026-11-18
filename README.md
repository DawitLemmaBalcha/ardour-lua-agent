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
| Import audio **or MIDI** (new track or selected track) | — | `import_media` (`import_audio` kept as alias) |
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
./bin/ardour_agent.py op import_media '{"path":"/music/basicpitch.mid"}'        # new MIDI track, waits
./bin/ardour_agent.py op import_media '{"path":"/music/take.wav","position":96000}'
./bin/ardour_agent.py op import_media '{"paths":["a.wav","b.wav"],"mode":"to_track"}'
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

**Audio import is possible, just not where you would look.** `Session:import_files`
is absent, but `Editor:do_import()` is exposed, together with `C.StringVector()`,
`Editing.*` import enums and `Temporal.timepos_t()`. `import_audio` uses it:

```json
{"path": "/music/take.wav", "position": 96000}                       // new track at 2 s
{"paths": ["a.wav", "b.wav"]}                                        // one track each
{"path": "take.wav", "mode": "to_track"}                             // into the SELECTED track
```

Import runs on worker threads. For `mode: "as_track"` the CLI **waits** before
returning (`--no-wait` opts out), using a track count **derived from the input
files** rather than from how many paths were passed — one file can become several
tracks:

| SMF track contains | Ardour track? |
|---|---|
| notes | yes |
| control change | yes |
| pitch bend | yes |
| channel pressure | yes |
| program change only | no |
| sysex only | no |
| tempo/meta only (conductor) | no |
| empty | no |

That table is measured, not assumed: each case was imported into Ardour 9.8 and
the resulting routes counted. `bin/smf.py` implements exactly this rule, so
`expected_tracks` is `Σ tracks-that-count` over MIDI inputs plus one per audio
file.

The wait requires the new-route set to be **stable for 0.35 s** *after* reaching
the expected count — the settle window is a safety net for anything the predictor
missed, never the proof itself. Verified live:

| inputs | expected | result |
|---|---|---|
| 3 single-track MIDI | 3 | 3 tracks, 1.1 s |
| 1 two-track MIDI | 2 | 2 tracks, 1.1 s |
| two-track MIDI + single-track MIDI | 3 | 3 tracks, 1.2 s |
| two-track MIDI + audio | 3 | 3 tracks, 1.1 s |
| 10 s audio + MIDI (either order) | 2 | 2 tracks, ~1.1 s |
| MIDI with only a conductor track | 0 | no tracks, exit 1 with a warning |

When the count cannot be derived — an unrecognised extension, an unparsable
file, or `merge: true` (grouping not verified) — the command **does not claim
success**: it prints `complete: false`, `uncertain: true`, explains why, and
exits non-zero. Over-delivery is treated the same way (`complete: false` with the
extra tracks listed), so a wrong prediction can never masquerade as a clean
import.

A `to_track` import creates no route, so completion cannot be observed from Lua;
the CLI says so and you verify with the native MCP `track_get_regions`.

MIDI import preserves the musical content exactly. A 6-note Basic-Pitch-style file
(six notes, four distinct durations, velocities 64–104, plus a pitch bend) round
-tripped as:

```
67 v88 @0.0 len 0.5    69 v76 @0.5 len 0.25   71 v96 @0.75 len 0.75
72 v64 @1.5 len 0.125  74 v104 @1.625 len 0.5 76 v72 @2.125 len 1.0
```

**Pitch bends are preserved.** Ardour writes the imported sources back into the
session (`interchange/<session>/midifiles/`); comparing those against the
originals shows every event surviving, with the bend's tick and value intact and
only the resolution rescaled (480 → 19200 ticks per quarter, exactly ×40):

| fixture | bend in source | in imported source | all events match |
|---|---|---|---|
| `bp_bend_after.mid` | tick 1920, value 10240 | tick 76800, value 10240 | ✔ |
| `bp_bend_mid.mid` | tick 240, value 16383 | tick 9600, value 16383 | ✔ |
| `bp_no_bend.mid` | none | none | ✔ |
| Basic Pitch file | tick 1500, value 10240 | tick 60000, value 10240 | ✔ |

10240 is 0x2800 (centre is 8192, so +25 % up); 16383 is 0x3FFF, full up.

Importing *into a named* track is still not possible: `Editor:set_selection()`
requires an internal `SelectionList` that Lua does not expose, so select the
target first (GUI, or MCP `track_select`) and use `mode: "to_track"`.

**Not exposed (confirmed absent):** playlist access from a track
(`Track:playlist()` is nil; `Session:playlists():by_name()` returns a null
pointer), region lists, `Session:tempo_map`, setting the track selection, and
reading automation back (`AutomationList:events()` is opaque). Use the native MCP
for the region operations it covers.

## Troubleshooting

`./bin/ardour_agent.py doctor` has two separate concerns: the **timer hook** is the
primary execution path and its round-trip is pass/fail; **OSC** is reported as
information (it is optional — needed only for direct OSC control and the fast
path).

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
