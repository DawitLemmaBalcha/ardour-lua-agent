# Attribution

This project adapts ideas and some documentation structure from:

**DuaneNielsen/ardour-claude-plugin** — https://github.com/DuaneNielsen/ardour-claude-plugin
Licensed MIT. Specifically:

* the concept of a Lua "remote execution" bridge driven from outside Ardour,
  with a request file and a response file (`lua/run_remote_lua.lua` upstream);
* the observation that Ardour Lua EditorActions are addressable over OSC as
  `LuaAction/script-N` (upstream `docs/adr-001-osc-bridge.md`);
* the operation-vs-reference split used for the skill's documentation.

Upstream's bridge is an EditorAction triggered by OSC. This project keeps that
as an optional fast path but adds:

* a **timer-hook trigger** that works on Ardour builds where the OSC action path
  is broken by upstream symbol duplication (verified on Fedora ardour9 9.8.0);
* a shared request processor (`lua/agent_core.lua`) used by both triggers;
* an operation registry (`lua/ardour_ops.lua`) with destructive-op guards;
* a Python client with atomic queue writes, request ids, and a `doctor` command;
* installation that binds the hook by writing `ui_scripts` directly, with no
  Script Manager clicks;
* every API claim re-verified against Ardour 9.8 and documented with the gaps.

Ardour itself is GPL-2.0-or-later; this project does not link against or modify
Ardour, it drives the public Lua/OSC surfaces, so it is distributed under MIT.
