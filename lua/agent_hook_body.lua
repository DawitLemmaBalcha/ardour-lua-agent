--[[
  agent_hook_body.lua — the body of the Ardour LuaHook instance.

  install.sh turns this file into a `<LuaCallback>` entry in ui_scripts,
  bound to Ardour's LuaTimerS / LuaTimerDS signals. Every tick it looks for a
  queued request and hands it to agent_core.lua. Everything is wrapped in
  pcall: an error must never let Ardour drop the hook.

  This file is an anonymous function *expression* (no `function name`), which
  is exactly what Ardour's hook loader compiles and calls.
]]
function ()
	local home = (os and os.getenv and os.getenv ("HOME")) or "/tmp"
	local state_dir = home .. "/.cache/ardour-agent"
	local data_dir = home .. "/.local/share/ardour-agent"

	local chunk = loadfile (data_dir .. "/agent_core.lua")
	if not chunk then
		return
	end
	local ok, process = pcall (chunk)
	if not ok or type (process) ~= "function" then
		return
	end
	pcall (process, state_dir, data_dir)
end
