--[[
  agent_bridge.lua — optional OSC fast path.

  Registered by install.sh as an EditorAction, which makes it reachable with

      /access_action  "LuaAction/script-<slot>"

  and drops end-to-end latency to a few milliseconds instead of the ~100 ms a
  timer hook needs. It is optional: on Ardour builds where `BasicUI::AccessAction`
  is duplicated between the executable and libardourcp.so (verified on the
  Fedora ardour9 9.8.0 build) the OSC module emits on a signal the Editor never
  listens to, so this action can never fire. The timer hook covers that case;
  both call the same agent_core.lua.
]]

ardour {
	["type"]    = "EditorAction",
	name        = "Ardour Agent Bridge",
	license     = "MIT",
	author      = "ardour-lua-agent",
	description = [[Process one queued request from bin/ardour_agent.py. Does nothing when the queue is empty.]]
}

function factory () return function ()
	local home = (os and os.getenv and os.getenv ("HOME")) or "/tmp"
	local state_dir = home .. "/.cache/ardour-agent"
	local data_dir = home .. "/.local/share/ardour-agent"

	local chunk = loadfile (data_dir .. "/agent_core.lua")
	if not chunk then return end
	local process = chunk ()
	process (state_dir, data_dir)
end end
