---
name: ardour-lua
description: "Drive a running Ardour 9 DAW with Lua through the ardour-lua-agent bridge: run arbitrary Lua inside Ardour, list/rename/delete tracks and buses, write gain/mute/solo automation, and discover the live Lua API. Use when the task needs an Ardour operation that the native Ardour MCP does not expose, when you must run or debug Lua inside Ardour, or when verifying what Ardour's Lua bindings actually provide on the installed version."
---

# Ardour Lua bridge

Ardour's native MCP server handles transport, tracks, regions, plugins, markers and
MIDI. This skill covers the second channel: **arbitrary Lua running inside the
Ardour process**. Use it for what MCP cannot do, and for discovering the API.

The bridge lives in the `ardour-lua-agent` project. `ARDOUR_AGENT` is the project
directory; the CLI is `$ARDOUR_AGENT/bin/ardour_agent.py`. When installed as a
DSH skill, check `~/.config/ardour9/ui_scripts` contains `Ardour Agent Timer`
before assuming it is set up; if missing, run `install.sh` and have the user
restart Ardour.

## Decide the channel

| Need | Use |
|---|---|
| transport, markers, fader/mute/solo, plugin add/remove/bypass, region move/split/gain, MIDI notes, session save | **native MCP** (`mcp__ardour__*`) |
| delete a track/bus, automation curves, arbitrary Lua, API discovery | **this skill** |
| audio import, playlist surgery | neither — GUI (Lua 9.8 exposes no import; no playlist accessor). Say so, don't fake it. |

Never modify Ardour's MCP server or duplicate a tool it already provides.

## Operating the bridge

```bash
B="$ARDOUR_AGENT/bin/ardour_agent.py"
$B doctor                      # install + process + OSC port + round-trip
$B ping                        # {ok, session, sample_rate}
$B ops                         # registered operations
$B op <name> '<json args>'
$B run script.lua              # arbitrary Lua; `return <value>` becomes JSON
echo 'return Session:name()' | $B run -
```

Every call returns JSON on stdout. Non-zero exit and a message on stderr means
the Lua itself failed — the error text is Ardour's own, including line numbers.

### Registered operations

* `tracks_list` → every route with `gain`, `muted`, `soloed`, `is_master`, `hidden`.
* `session_info` → name, path, sample rate, route/track counts.
* `track_delete {name, dry_run}` → removes a track or bus. **Refuses the master
  bus.** Always call with `dry_run: true` first, show the user what would go, and
  only then repeat without it.
* `automation_add {name, param: gain|mute|solo, points: [{when: <samples>, value: 0..1}]}`
* `automation_clear {name, param, dry_run}` → clears the whole automation list.
* `ping`, `list_ops`.

`when` is in **samples** (`sample_rate × seconds`). Values are normalised: gain
0..1 (1.0 = unity), mute/solo 0 or 1. Automation is write-only through Lua — you
cannot read points back; confirm intent with the user instead of "verifying".

## Writing Lua

The chunk runs with Ardour's bindings in scope and `return` value serialised as
JSON. `print()` output comes back as `log` lines on stderr.

```lua
local out = {}
for r in Session:get_routes ():iter () do
  out[#out + 1] = { name = r:name (), gain = r:gain_control ():get_value () }
end
return out
```

Verified API on Ardour 9.8 is in `references/lua-api.md`. Key idioms:

* Iterate with `:iter()`: `for r in Session:get_routes ():iter () do ... end`.
* Time objects are mandatory: `al:add (Temporal.timepos_t (sample), value)`,
  `al:clear (Temporal.timepos_t (0), Temporal.timepos_t (1000000000000))`.
* Guard optional methods: `type (o.method) == "function"` before calling.
* Method guards written as `obj:method and obj:method ()` are a **syntax error**
  in Lua — write `type (obj.method) == "function"`.

## Discovering instead of assuming

The bindings differ per version. Probe before writing an op:

```bash
$B run - <<'LUA'
local function probe (o, names)
  local r = {}
  for _, n in ipairs (names) do
    local ok, v = pcall (function () return o[n] end)
    r[n] = (ok and type (v)) or "ERR"
  end
  return r
end
local t; for r in Session:get_tracks ():iter () do t = r break end
return { session = probe (Session, {"get_routes","remove_route","import_files","tempo_map"}),
         track = t and probe (t, {"name","playlist","gain_control"}) or nil }
LUA
```

`getmetatable` is protected (returns a boolean), so methods cannot be enumerated —
probe candidate names.

## Safety

* Destructive ops: `dry_run` first, name the exact route, get confirmation.
* Never delete `Master`; the op refuses, but do not test that on a user's session.
* Do not call `save_state` or overwrite the session unless asked.
* Leave playback stopped and the fader where it was; automation tests on a real
  track should be cleared afterwards.
* If the hook is not loaded (no `Ardour Agent Timer` in `ui_scripts`, or `doctor`
  fails), stop and fix the install rather than trying GUI automation.

## Troubleshooting

`doctor` covers most failures. The two that matter:

* **No response** — Ardour restarted since install, or the hook did not load.
  Re-run `install.sh` with Ardour closed, then restart it.
* **`/access_action` does nothing** — expected on Fedora `ardour9` 9.8.0: the
  symbol `BasicUI::AccessAction` is duplicated between the binary and
  `libardourcp.so`. The timer hook is the working path; don't chase OSC actions.

More: `$ARDOUR_AGENT/docs/troubleshooting.md`.
