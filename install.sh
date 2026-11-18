#!/usr/bin/env bash
# Install the Ardour agent bridge into a stock Ardour 9 setup.
#
#   ./install.sh            # install / update
#   ./install.sh --check    # report what is installed, change nothing
#   ./install.sh --force    # install even if Ardour is running
#
# What it touches (all user-owned, all backed up before writing):
#   ~/.config/ardour9/scripts/ardour_agent_bridge.lua   optional OSC fast path
#   ~/.config/ardour9/ui_scripts                        timer hook + action slot
#   ~/.local/share/ardour-agent/                        agent_core.lua, ardour_ops.lua
#
# It never modifies the Ardour binary, its native MCP server, or any system file.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

CONFIG_DIR="${ARDOUR_AGENT_CONFIG_DIR:-$HOME/.config/ardour9}"
DATA_DIR="${ARDOUR_AGENT_DATA:-$HOME/.local/share/ardour-agent}"
STATE_DIR="${ARDOUR_AGENT_STATE:-$HOME/.cache/ardour-agent}"
SCRIPTS_DIR="$CONFIG_DIR/scripts"
BRIDGE="$SCRIPTS_DIR/ardour_agent_bridge.lua"

if [[ ! -d "$CONFIG_DIR" && -d "$HOME/.config/ardour8" && -z "${ARDOUR_AGENT_CONFIG_DIR:-}" ]]; then
  CONFIG_DIR="$HOME/.config/ardour8"
  SCRIPTS_DIR="$CONFIG_DIR/scripts"
  BRIDGE="$SCRIPTS_DIR/ardour_agent_bridge.lua"
  echo "note: no ~/.config/ardour9, using Ardour 8 config dir $CONFIG_DIR"
fi

if [[ $CHECK -eq 1 ]]; then
  echo "config dir : $CONFIG_DIR"
  [[ -f "$BRIDGE" ]] && echo "[ok]   action bridge   $BRIDGE" || echo "[MISS] action bridge   $BRIDGE"
  [[ -f "$DATA_DIR/agent_core.lua" ]] && echo "[ok]   core            $DATA_DIR/agent_core.lua" || echo "[MISS] core            $DATA_DIR/agent_core.lua"
  [[ -f "$DATA_DIR/ardour_ops.lua" ]] && echo "[ok]   ops             $DATA_DIR/ardour_ops.lua" || echo "[MISS] ops             $DATA_DIR/ardour_ops.lua"
  if grep -q 'Ardour Agent Timer' "$CONFIG_DIR/ui_scripts" 2>/dev/null; then
    echo "[ok]   timer hook      bound in ui_scripts"
  else
    echo "[MISS] timer hook      not in ui_scripts"
  fi
  exit 0
fi

if ps -eo comm= | grep -qE '^(ArdourGUI|ardour-[0-9])' && [[ $FORCE -eq 0 ]]; then
  cat >&2 <<'EOF'
Ardour is running. Quit Ardour first: it rewrites ui_scripts on exit and would
overwrite what this installer writes. Then re-run ./install.sh, and start Ardour
again (the hook is read at startup).

(Or pass --force if you know Ardour will not save state, then restart it.)
EOF
  exit 1
fi

mkdir -p "$SCRIPTS_DIR" "$DATA_DIR" "$STATE_DIR"
STAMP="$(date +%Y%m%d%H%M%S)"

backup () {
  local f="$1"
  if [[ -f "$f" ]]; then
    cp -p "$f" "$f.bak-$STAMP" && echo "backed up $(basename "$f") -> $(basename "$f").bak-$STAMP"
  fi
  return 0
}

echo "==> installing Lua"
install -m 0644 "$HERE/lua/agent_core.lua" "$DATA_DIR/agent_core.lua"
install -m 0644 "$HERE/lua/ardour_ops.lua" "$DATA_DIR/ardour_ops.lua"
install -m 0644 "$HERE/lua/agent_bridge.lua" "$BRIDGE"

echo "==> binding timer hook and OSC action slot"
backup "$CONFIG_DIR/ui_scripts"
python3 - "$CONFIG_DIR" "$BRIDGE" "$HERE/lua/agent_hook_body.lua" "$STATE_DIR" <<'PY'
import base64, os, re, sys
import xml.etree.ElementTree as ET

config_dir, bridge_path, hook_body_path, state_dir = sys.argv[1:5]
ui_path = os.path.join(config_dir, "ui_scripts")
NAME = "Ardour Agent Timer"
ACTION_NAME = "Ardour Agent Bridge"
MAX_SLOTS = 32

# LuaSignal order for Ardour 9.x (gtk2_ardour/luasignal_syms.inc.h, N = 54).
#   index 50 = LuaTimerS, index 51 = LuaTimerDS
# std::bitset::to_string() prints the highest bit first, so index i lives at
# string position (N - 1 - i). Override for a different signal layout.
N_SIGNALS = 54
SIGNALS = os.environ.get("ARDOUR_AGENT_HOOK_SIGNALS", "00" + "11" + "0" * (N_SIGNALS - 4))

if os.path.exists(ui_path):
    tree = ET.parse(ui_path)
    root = tree.getroot()
else:
    root = ET.Element("UIScripts")
    tree = ET.ElementTree(root)


def lua_str(value: str) -> str:
    out = ["\""]
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("\\%03d" % ord(ch))
        else:
            out.append(ch)
    out.append("\"")
    return "".join(out)


def append_b64_text(node, chunk: str, encoding: str) -> None:
    # ActionScript payloads may embed Lua bytecode, so bytes are round-tripped
    # through latin-1; LuaCallback payloads are pure ASCII source.
    raw = chunk.encode(encoding, "surrogateescape")
    node.text = base64.b64encode(raw).decode("ascii")


# ---- 1. timer hook ---------------------------------------------------------
hooks = root.find("ActionHooks")
if hooks is None:
    hooks = ET.SubElement(root, "ActionHooks")

for cb in list(hooks.findall("LuaCallback")):
    if cb.get("name") == NAME:
        hooks.remove(cb)

body = open(hook_body_path, encoding="utf-8").read().strip()
header = (
    'ardour {\n'
    '  ["type"] = "LuaHook",\n'
    '  name = "Ardour Agent Timer",\n'
    '  license = "MIT",\n'
    '  author = "ardour-lua-agent",\n'
    '  description = "Process queued requests from bin/ardour_agent.py on every timer tick."\n'
    '}\n\n'
)
script_src = header + "function factory () return " + body + " end\n"
instance_src = "return " + body

state_chunk = (
    "s = {}\n"
    f"s['n'] = {lua_str(NAME)}\n"
    f"s['s'] = {lua_str(script_src)}\n"
    f"s['f'] = {lua_str(instance_src)}\n"
    "s['a'] = {}\n"
)

cb = ET.SubElement(hooks, "LuaCallback", {
    "lua": "Lua 5.3",
    "id": "900001",
    "name": NAME,
    "signals": SIGNALS,
})
append_b64_text(cb, state_chunk, "utf-8")
print(f"hook     {NAME}: LuaTimerS + LuaTimerDS (signals={SIGNALS})")

# ---- 2. optional OSC fast path (EditorAction) ------------------------------
action = root.find("ActionScript")
if action is None:
    action = ET.SubElement(root, "ActionScript", {"lua": "Lua 5.3"})

chunk_text = ""
if action.text and action.text.strip():
    try:
        chunk_text = base64.b64decode(action.text.strip()).decode("latin-1")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"cannot decode existing ui_scripts ActionScript payload: {exc}")

slot = None
if bridge_path in chunk_text:
    m = re.search(r"scripts\[(\d+)\]\[\"a\"\]\[\"x-script-origin\"\] = " + re.escape(lua_str(bridge_path)), chunk_text)
    if m:
        slot = int(m.group(1))
    print(f"action   already bound ({bridge_path})")
else:
    used = sorted({int(m) for m in re.findall(r"scripts\[(\d+)\]", chunk_text)})
    slot = (used[-1] + 1) if used else 1
    if slot > MAX_SLOTS:
        sys.exit(f"all {MAX_SLOTS} action slots are taken; free one in Script Manager")
    if not chunk_text.strip():
        chunk_text = "scripts = {}\n"
    chunk_text += (
        f"\nscripts[{slot}] = {{}}\n"
        f"scripts[{slot}][\"a\"] = {{}}\n"
        f"scripts[{slot}][\"a\"][\"x-script-origin\"] = {lua_str(bridge_path)}\n"
        f"scripts[{slot}][\"n\"] = {lua_str(ACTION_NAME)}\n"
        f"scripts[{slot}][\"s\"] = \"\"\n"
        f"scripts[{slot}][\"f\"] = \"\"\n"
    )
    print(f"action   {ACTION_NAME}: LuaAction/script-{slot}")

append_b64_text(action, chunk_text, "latin-1")

tree.write(ui_path, encoding="UTF-8", xml_declaration=True)

os.makedirs(state_dir, exist_ok=True)
with open(os.path.join(state_dir, "slot"), "w") as fh:
    fh.write(str(slot or 1) + "\n")
PY

chmod +x "$HERE/bin/ardour_agent.py" 2>/dev/null || true
cat <<EOF

Installed.

  hook       ~/.config/ardour9/ui_scripts  (LuaTimerS + LuaTimerDS, ~100 ms)
  action     $BRIDGE  (OSC fast path, optional)
  core       $DATA_DIR/agent_core.lua
  ops        $DATA_DIR/ardour_ops.lua
  OSC        127.0.0.1:3819
  slot       $(cat "$STATE_DIR/slot" 2>/dev/null || echo 1)

Next:
  1. Start Ardour (the hook is read at startup).
  2. Run: $HERE/bin/ardour_agent.py doctor
  3. Run: $HERE/bin/ardour_agent.py ping
EOF
