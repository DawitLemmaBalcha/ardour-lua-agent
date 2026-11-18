# Ardour 9.8 Lua API — verified surface

Probed live against `ardour9-9.8.0-2.fc45` by running Lua through the bridge.
`type` is what the binding reports. Anything absent here was **nil** when probed;
do not assume it exists.

## Session

| Member | Type | Notes |
|---|---|---|
| `name`, `path`, `sample_rate` | function | plain getters |
| `get_routes()` | function | → RouteList; iterate with `:iter()` |
| `get_tracks()` | function | → TrackList; Track adds no extra methods over Route |
| `playlists()` | function | → PlaylistSet; only `by_name()`; **returns null for real playlists** |
| `remove_route(route)` | function | works — basis of `track_delete` |
| `remove_routes({route, ...})` | function | fallback |
| `new_audio_track(...)`, `new_midi_track(...)` | function | signatures not yet mapped; MCP `tracks_add` is easier |
| `locations()` | function | markers/locations |
| `save_state()` | function | writes the session; treat as destructive |
| `undo()`, `redo()` | nil | **not bound** — use MCP `session_undo` / `session_redo` |

Absent: `import_files`, `tempo_map`, `set_tempo`, `set_meter`, `duplicate_route`,
`get_playlist_by_name`, `dirty`, `snapshot`, `track_by_name`.

## Route / Track

| Member | Type |
|---|---|
| `name()`, `set_name(s)` | function |
| `gain_control()`, `mute_control()`, `solo_control()`, `rec_enable_control()` | function → Controllable |
| `is_master()`, `is_hidden()`, `is_monitor()`, `is_auditioner()` | function → bool |
| `nth_plugin(i)` | function → Processor (nil-terminated is unreliable; cap the loop) |
| `add_processor(...)`, `remove_processor(p)` | function |

Absent: `is_track`, `is_midi_track`, `is_vca`, `playlist`, `set_playlist`,
`plugins`, `sends`, `ninputs`, `noutputs`, `id`, `type`, `presentation_info`.
To tell a track from a bus, compare `Session:get_tracks()` membership.

## Controllable

`get_value()`, `set_value(v)`, `alist()` (→ AutomationList), `name()`.
Absent: `automation`, `interface`, `set_value_db`.

## AutomationList

| Call | Verified |
|---|---|
| `add(Temporal.timepos_t(sample), value)` | ✔ values are normalised (gain 0..1) |
| `clear(Temporal.timepos_t(0), Temporal.timepos_t(1e12))` | ✔ takes **two** timepos_t |
| `set_interpolation(mode)` | ✔ |
| `clear()` with no args | ✘ raises "bad argument #2 … got string" |
| `events()` | returns opaque userdata — **not readable** (`pairs`, `#` both fail) |

## Editor

| Member | Verified |
|---|---|
| `do_import(files, file_mode, track_mode, src_quality, midi_ns, midi_tm, pos, PluginInfo(), Track(), false)` | ✔ imports audio from the timer hook |
| `undo()`, `redo()` | ✔ |
| `get_selection()` | ✔ returns an opaque SelectionList (not iterable) |
| `set_selection(list, n)` | ✘ needs a `SelectionList`; tables and Route objects are rejected |
| `select_all_tracks()` | ✔ (present) |

Absent: `import_audio`, `import_files`, `insert_external_audio`, `add_region`,
`new_region`, `split_regions`, `select_all`, `playlists`, `save_state`.

### Import recipe (from Ardour's own `s_import_files.lua`)

```lua
local files = C.StringVector ()
files:push_back ("/path/to.wav")
Editor:do_import (files,
  Editing.ImportDistinctFiles,      -- or Editing.ImportMergeFiles
  Editing.ImportAsTrack,            -- or Editing.ImportToTrack (uses SELECTION)
  ARDOUR.SrcQuality.SrcBest,
  ARDOUR.MidiTrackNameSource.SMFFileAndTrackName,
  ARDOUR.MidiTempoMapDisposition.SMFTempoIgnore,
  Temporal.timepos_t (48000),       -- placement, samples
  ARDOUR.PluginInfo (), ARDOUR.Track (), false)
```

`ImportToTrack` honours the current selection; there is no way to select from Lua,
so callers must select first (GUI or native MCP `track_select`). Import is
asynchronous — regions appear after the worker finishes.

Pre-flight checks: `ARDOUR.LuaAPI.file_test(path, ARDOUR.LuaAPI.FileTest.Exists)`
returns true/false; `ARDOUR.LuaAPI.monotonic_time()` is available for timing.

## Temporal

`timepos_t(sample)` → userdata (works). Also exposes `TempoMap`, `Tempo`,
`Meter`, `Beats`, `BBT_TIME`, `superclock_ticks_per_second()`. The tempo map is
not reachable from `Session`, so tempo edits were not implemented.

## Global names

`ARDOUR`, `ArdourUI`, `C`, `Cairo`, `Editing`, `Editor`, `Evoral`, `Keyboard`,
`LuaDialog`, `LuaSignal`, `PBD`, `Session`, `Temporal`, `Timecode`, `Vamp`,
`scripts`, plus the standard `io`, `os`, `math`, `string`, `table`, `utf8`,
`bit32`.

`getmetatable(x)` returns a **boolean**, so class methods cannot be enumerated;
probe candidate names instead.

## Gotchas that cost real time

1. `obj:method and obj:method()` is a **syntax error** in Lua (a method call
   requires parentheses). Guard with `type (obj.method) == "function"`.
2. `Session:get_routes()` is not iterable directly — call `:iter()`.
3. `AutomationList:add` rejects numbers; wrap in `Temporal.timepos_t`.
4. `Session:playlists():by_name()` returns a wrapper whose shared_ptr is null for
   ordinary track playlists — region access is effectively unavailable.
