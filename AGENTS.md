# ardour-lua-agent

Two independent channels drive Ardour. This repo owns the **Lua channel**; never
modify or duplicate the native one.

## Map

- `bin/ardour_agent.py` — CLI client (`doctor`, `ping`, `ops`, `op`, `run`).
- `bin/osc.py` — minimal OSC codec used for the optional fast path.
- `lua/agent_core.lua` — request → response processor, shared by both triggers.
- `lua/ardour_ops.lua` — operation registry (the thing you extend).
- `lua/agent_hook_body.lua` — 100 ms timer-hook body (default trigger).
- `lua/agent_bridge.lua` — OSC `LuaAction/script-N` EditorAction (fast path).
- `install.sh` — copies files, binds the hook into `ui_scripts`, backs up.
- `skill/ardour-lua/` — the agent skill installed into `~/.dsh/skills`.
- `docs/troubleshooting.md` — failure modes, including the upstream OSC bug.

## Rules

- Ardour must be closed to install; the hook is read at startup.
- Never duplicate a native MCP tool. Add only capabilities MCP lacks.
- New ops go in `lua/ardour_ops.lua`, guarded with `type (obj.m) == "function"`.
- Time arguments are `Temporal.timepos_t(samples)`, never raw numbers.
- Re-verify the Lua surface against the installed Ardour before trusting
  `skill/ardour-lua/references/lua-api.md`; probe with `ardour_agent.py run`.

## Checks

- `./bin/ardour_agent.py doctor` — install + process + port + round-trip.
- `./bin/ardour_agent.py op tracks_list` — smoke test.
- `./install.sh --check` — what is installed, without changing anything.
- `python3 -m unittest discover -s tests -v` — offline tests for the SMF
  import predictor (`bin/smf.py`); no Ardour required.
