--[[
  ardour_ops.lua — operation registry loaded by agent_core.lua.

  Plain Lua library, no `ardour { ... }` header on purpose: it is not an
  EditorAction, so it never appears in Ardour's Script Manager. Add an op by
  writing a function and registering it in `M.ops` at the bottom.

  Everything here was verified against Ardour 9.8.0 (Fedora ardour9 package).
  Methods that the 9.8 Lua bindings do *not* expose are documented in
  README "What the Lua API exposes"; do not assume they exist.
]]

local M = {}

-- ---------------------------------------------------------------- helpers ---

--- Call a method only if it exists; a missing binding must never abort an op.
local function call (obj, method, ...)
	local fn = obj[method]
	if type (fn) ~= "function" then return nil end
	local ok, rv = pcall (fn, obj, ...)
	if ok then return rv end
	return nil
end

local function truthy (obj, method)
	return call (obj, method) == true
end

--- Value of a control returned by `method` (e.g. mute_control), or nil.
local function ctrl (obj, method)
	local c = call (obj, method)
	if not c then return nil end
	return call (c, "get_value")
end

function M.fail (msg)
	error (msg, 2)
end

function M.require_arg (args, key)
	local v = args[key]
	if v == nil or v == "" then
		M.fail ("missing required argument '" .. key .. "'")
	end
	return v
end

function M.bool (v, default)
	if v == nil then return default end
	if type (v) == "boolean" then return v end
	if type (v) == "string" then return v == "true" or v == "1" or v == "yes" end
	return v ~= 0
end

--- Every route except monitor/auditioner.
function M.routes ()
	local out = {}
	for route in Session:get_routes ():iter () do
		if not truthy (route, "is_monitor") and not truthy (route, "is_auditioner") then
			out[#out + 1] = route
		end
	end
	return out
end

function M.route_summary (route)
	local out = {
		name = call (route, "name"),
		is_master = truthy (route, "is_master"),
		hidden = truthy (route, "is_hidden"),
		gain = ctrl (route, "gain_control"),
		muted = ctrl (route, "mute_control") == true,
		soloed = ctrl (route, "solo_control") == true,
	}
	return out
end

--- Resolve a route by exact name, then case-insensitively.
function M.route_by_name (name)
	M.require_arg ({ name = name }, "name")
	local routes = M.routes ()
	for _, route in ipairs (routes) do
		if call (route, "name") == name then return route end
	end
	for _, route in ipairs (routes) do
		local n = call (route, "name")
		if n and n:lower () == tostring (name):lower () then return route end
	end
	M.fail ("no route named '" .. tostring (name) .. "'")
end

function M.route_from_args (args)
	return M.route_by_name (args.name or args.route or args.track)
end

-- ------------------------------------------------------------------- ops ---

M.ops = {}

M.ops["ping"] = function ()
	return { ok = true, session = Session:name (), sample_rate = Session:sample_rate () }
end

M.ops["list_ops"] = function ()
	local names = {}
	for k in pairs (M.ops) do names[#names + 1] = k end
	table.sort (names)
	return { ops = names, count = #names }
end

M.ops["session_info"] = function ()
	local tracks, total = 0, 0
	for _ in Session:get_routes ():iter () do total = total + 1 end
	for _ in Session:get_tracks ():iter () do tracks = tracks + 1 end
	return {
		name = Session:name (),
		path = Session:path (),
		sample_rate = Session:sample_rate (),
		routes = total,
		tracks = tracks,
	}
end

M.ops["tracks_list"] = function ()
	local out = {}
	for _, route in ipairs (M.routes ()) do
		out[#out + 1] = M.route_summary (route)
	end
	return { count = #out, routes = out }
end

M.ops["track_delete"] = function (args)
	local route = M.route_from_args (args)
	local name = call (route, "name")
	if truthy (route, "is_master") then
		M.fail ("refusing to delete the master bus")
	end
	if args.dry_run then
		return { dry_run = true, would_delete = name }
	end
	local ok = pcall (function () Session:remove_route (route) end)
	if not ok then
		ok = pcall (function () Session:remove_routes ({ route }) end)
	end
	if not ok then
		M.fail ("could not remove route '" .. name .. "'")
	end
	return { ok = true, deleted = name, remaining = #M.routes () }
end

--- Control names accepted by the automation ops.
local CONTROLS = {
	gain = "gain_control",
	mute = "mute_control",
	solo = "solo_control",
}

local function automation_list (args)
	local param = (args.param or "gain"):lower ()
	local method = CONTROLS[param]
	if not method then
		M.fail ("param must be one of: gain, mute, solo")
	end
	local route = M.route_from_args (args)
	local c = call (route, method)
	if not c then M.fail ("route has no " .. param .. " control") end
	local al = call (c, "alist")
	if not al then M.fail ("no automation list for " .. param) end
	return route, al, param
end

M.ops["automation_clear"] = function (args)
	local route, al, param = automation_list (args)
	local name = call (route, "name")
	if args.dry_run then
		return { dry_run = true, would_clear = param, route = name }
	end
	al:clear (Temporal.timepos_t (0), Temporal.timepos_t (1000000000000))
	return { ok = true, cleared = param, route = name }
end

--- Add automation points. points = [{ when = <samples>, value = <0..1> }, ...]
--- Note: the 9.8 Lua bindings expose write access (add/clear) but `events()`
--- returns an opaque userdata, so automation cannot be read back from Lua.
M.ops["automation_add"] = function (args)
	local route, al, param = automation_list (args)
	local points = args.points
	if type (points) ~= "table" or #points == 0 then
		M.fail ("pass points=[{when=<samples>, value=<0..1>}, ...]")
	end
	local name = call (route, "name")
	local added = 0
	for _, p in ipairs (points) do
		local when = tonumber (p.when or p.sample)
		local value = tonumber (p.value)
		if when == nil or value == nil then
			M.fail ("each point needs numeric 'when' and 'value'")
		end
		al:add (Temporal.timepos_t (when), value)
		added = added + 1
	end
	return { ok = true, route = name, param = param, added = added }
end

-- --------------------------------------------------------------- dispatch ---

function M.dispatch (name, args)
	local fn = M.ops[name]
	if not fn then
		local known = {}
		for k in pairs (M.ops) do known[#known + 1] = k end
		table.sort (known)
		return { ok = false, error = "unknown op '" .. tostring (name) .. "'", available = known }
	end
	local ok, rv = pcall (fn, args or {})
	if not ok then
		return { ok = false, error = tostring (rv) }
	end
	return rv
end

return M
