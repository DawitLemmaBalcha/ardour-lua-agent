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

### MIDI specifics (verified)

* A `.mid` imports as a **MIDI track**; with `MidiTrackNameSource.SMFFileAndTrackName`
  the route is named `<file>-<n>-<track>`, e.g. `basicpitch_hum-1-t1`, and the
  region after the SMF name (`basicpitch_hum-1`).
* Notes survive exactly: pitch, velocity, start beat and length beat all matched
  the source; a 6-note file with four distinct durations came through unchanged.
* One MIDI file may contain several SMF tracks; each becomes its own Ardour track
  under `ImportDistinctFiles` (this is what the `-t1` suffix indexes) — but only
  tracks with MIDI *model* events. Measured: notes, CC, pitch bend and channel
  pressure each create a track; program-change-only, sysex-only, conductor
  (meta) and empty tracks are dropped. `bin/smf.py` predicts the count.
* Completion is atomic: the route, region and notes become visible together, so a
  new route is a safe completion signal for `ImportAsTrack`.
* Ardour writes imported sources into `interchange/<session>/midifiles/`
  (and `audiofiles/` for audio). Those files are the imported data; parsing them
  is the only way to verify events the API does not expose, such as pitch bends.
* Only SMF Type 0 and Type 1 import. Any other format value hits
  `"MIDI file has unsupported SMF format type N"` in `libs/ardour/import.cc` and
  produces no tracks — the switch is `case 0 / case 1 / default`, so a header
  claiming format 3 is unsupported too.
* SMF resolution is rescaled on import: 480 ticks/quarter becomes 19200
  (×40), and every event tick scales with it; values are untouched.
* `do_import`'s last parameter is `with_markers`, **not** "add to selection".
  Imported regions are therefore not selected.

### Do not crash Ardour

`RegionSelection:regionlist()` returns a `RegionList` bound as a C++ list.
Calling `front()` on it while empty hits `assert(!this->empty())` and aborts the
process — no Lua error, no recovery. Guard with `n_midi_regions() > 0` first.
`Editor:get_regions_at()` needs an existing `RegionSelection` and `TrackViewList`,
so regions are only reachable after a GUI selection exists.

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
