--[[
  agent_core.lua — request processor shared by both triggers.

  Returns a function that consumes one queued request and writes its reply:

      local process = dofile ("<data-dir>/agent_core.lua")
      process ("<state-dir>", "<data-dir>")

  Two entry points call this:
    * agent_hook_body.lua  — an Ardour LuaHook on the 100 ms / 1 s timers
                             (default; works on every build, ~100 ms latency)
    * agent_bridge.lua     — an EditorAction driven by OSC /access_action
                             (faster, but inert on Ardour builds where
                             BasicUI::AccessAction is duplicated between the
                             main binary and libardourcp.so — see README)

  The request file is consumed before execution, so a second trigger cannot
  replay it. The reply is written to a temp file and renamed into place, so
  the client never reads a partial reply.
]]

return function (state_dir, data_dir)
	local request = state_dir .. "/request.lua"
	local response = state_dir .. "/response.txt"

	if not io then
		return -- sandboxed Lua strips io; the bridge cannot work (see README)
	end

	local fin = io.open (request, "r")
	if not fin then
		return
	end
	local src = fin:read ("*all")
	fin:close ()
	os.remove (request)

	-- first line carries the request id, the rest is Lua
	local id = "unknown"
	local first_line = src:match ("^([^\n]*)")
	if first_line then
		local captured = first_line:match ("^%-%-%s*ardour%-agent%-id:%s*([%w%-]+)")
		if captured then
			id = captured
			src = src:sub (#first_line + 2)
		end
	end

	-- ---- JSON encoder -----------------------------------------------------
	local function esc (s)
		s = s:gsub ("[\\\"\n\r\t]", {
			["\\"] = "\\\\", ['"'] = '\\"', ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
		})
		s = s:gsub ("%c", function (c) return string.format ("\\u%04x", c:byte ()) end)
		return s
	end

	local function is_array (t)
		local n = 0
		for k in pairs (t) do
			if type (k) ~= "number" or k < 1 or k % 1 ~= 0 then return false end
			n = n + 1
		end
		return n == #t
	end

	local encode
	encode = function (v)
		local tv = type (v)
		if v == nil then return "null" end
		if tv == "boolean" then return v and "true" or "false" end
		if tv == "number" then
			if v % 1 == 0 and math.abs (v) < 1e15 then return string.format ("%d", v) end
			return string.format ("%.14g", v)
		end
		if tv == "string" then return '"' .. esc (v) .. '"' end
		if tv == "table" then
			local out = {}
			if is_array (v) then
				for i = 1, #v do out[i] = encode (v[i]) end
				return "[" .. table.concat (out, ",") .. "]"
			end
			for k, val in pairs (v) do
				out[#out + 1] = '"' .. esc (tostring (k)) .. '":' .. encode (val)
			end
			table.sort (out)
			return "{" .. table.concat (out, ",") .. "}"
		end
		return '"<' .. tv .. '>"'
	end

	-- ---- execute ----------------------------------------------------------
	local logs = {}
	local real_print = print
	_G.print = function (...)
		local parts = {}
		for i = 1, select ("#", ...) do
			parts[i] = tostring ((select (i, ...)))
		end
		logs[#logs + 1] = table.concat (parts, "\t")
	end

	_G.agent_op = function (name, args)
		local chunk, err = loadfile (data_dir .. "/ardour_ops.lua")
		if not chunk then error ("cannot load ardour_ops.lua: " .. tostring (err)) end
		return chunk ().dispatch (name, args or {})
	end

	local loader = load or loadstring
	local fn, compile_err = loader (src, "agent-request", "t", _G)

	local status, payload
	if not fn then
		status, payload = "error", "compile error: " .. tostring (compile_err)
	else
		local ok, rv = pcall (fn)
		if ok then
			status, payload = "ok", encode (rv)
		else
			status, payload = "error", tostring (rv)
		end
	end

	_G.print = real_print
	_G.agent_op = nil

	local body = { "id: " .. id, "status: " .. status, "---", payload }
	if #logs > 0 then
		body[#body + 1] = "log:"
		for _, line in ipairs (logs) do
			body[#body + 1] = line
		end
	end

	local tmp = response .. ".tmp"
	local fout = io.open (tmp, "w")
	if not fout then return end
	fout:write (table.concat (body, "\n"))
	fout:write ("\n")
	fout:close ()
	os.rename (tmp, response)
end
