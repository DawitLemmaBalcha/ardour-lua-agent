#!/usr/bin/env python3
"""ardour_agent — command line client for the Ardour Lua bridge.

Two ways to run Lua inside a live Ardour session:

    ardour_agent.py ping
    ardour_agent.py run  my_script.lua
    ardour_agent.py run -                      # from stdin
    ardour_agent.py op   track_delete '{"name":"Drums"}'
    ardour_agent.py ops                        # list registered operations
    ardour_agent.py doctor                     # diagnose the whole setup

The transport is a single OSC message (`/access_action LuaAction/script-<slot>`)
plus two files in ~/.cache/ardour-agent. Nothing listens on the network beyond
Ardour's own OSC server, and Ardour is not patched.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from osc import encode_message  # noqa: E402
from smf import SmfError, UnsupportedSmfFormat, model_track_count, smf_format  # noqa: E402

HOME = os.path.expanduser("~")
STATE_DIR = os.environ.get("ARDOUR_AGENT_STATE", os.path.join(HOME, ".cache", "ardour-agent"))
DATA_DIR = os.environ.get("ARDOUR_AGENT_DATA", os.path.join(HOME, ".local", "share", "ardour-agent"))
SCRIPTS_DIR = os.environ.get(
    "ARDOUR_AGENT_SCRIPTS", os.path.join(HOME, ".config", "ardour9", "scripts")
)
HOST = os.environ.get("ARDOUR_AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARDOUR_AGENT_OSC_PORT", "3819"))


def _installed_slot() -> int:
    """Slot written by install.sh; env var wins so a user can move the binding."""
    env = os.environ.get("ARDOUR_AGENT_SLOT")
    if env:
        return int(env)
    try:
        with open(os.path.join(STATE_DIR, "slot")) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return 1


SLOT = _installed_slot()
REQUEST = os.path.join(STATE_DIR, "request.lua")
RESPONSE = os.path.join(STATE_DIR, "response.txt")


class BridgeError(RuntimeError):
    pass


def _osc_port_listening(port: int) -> bool:
    """True when a UDP socket is bound to `port` (hex port in /proc/net/udp*)."""
    needle = f":{port:04X}"
    for path in ("/proc/net/udp", "/proc/net/udp6"):
        try:
            with open(path) as fh:
                next(fh, None)
                for line in fh:
                    fields = line.split()
                    if len(fields) > 3 and fields[1].endswith(needle) and fields[3] == "07":
                        return True
        except OSError:
            continue
    return False


def _ardour_running() -> bool:
    """Match the process name, never the command line (which would match us)."""
    try:
        out = os.popen("ps -eo comm= 2>/dev/null").read()
    except OSError:
        return False
    return any(line.startswith(("ArdourGUI", "ardour-9")) for line in out.splitlines())


def send_trigger(slot: int = SLOT) -> None:
    message = encode_message("/access_action", f"LuaAction/script-{slot}")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(message, (HOST, PORT))


def run_lua(source: str, timeout: float = 15.0, poll: float = 0.02) -> dict:
    """Queue `source` as a request, trigger the bridge, return the parsed reply."""
    os.makedirs(STATE_DIR, exist_ok=True)
    request_id = uuid.uuid4().hex[:16]
    header = f"-- ardour-agent-id: {request_id}\n"
    tmp = REQUEST + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write(source)
        if not source.endswith("\n"):
            fh.write("\n")
    os.replace(tmp, REQUEST)

    try:
        os.unlink(RESPONSE)
    except FileNotFoundError:
        pass

    send_trigger()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(RESPONSE, encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            time.sleep(poll)
            # re-trigger occasionally: a trigger sent while Ardour was busy is
            # dropped silently, and the file is already consumed by then.
            if time.monotonic() + 1.0 > deadline:
                send_trigger()
            continue
        if not text:
            time.sleep(poll)
            continue
        parsed = _parse_response(text)
        if parsed["id"] != request_id:
            time.sleep(poll)
            continue
        return parsed

    raise BridgeError(
        "no response from the Ardour bridge.\n"
        f"  request: {REQUEST}\n"
        f"  osc:     {HOST}:{PORT} -> LuaAction/script-{SLOT}\n"
        "Run `ardour_agent.py doctor` for a diagnosis."
    )


def _parse_response(text: str) -> dict:
    lines = text.splitlines()
    header: dict[str, str] = {}
    body_start = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == "---":
            body_start = i + 1
            break
        if ":" in line:
            key, _, value = line.partition(":")
            header[key.strip()] = value.strip()
    body_lines = lines[body_start:]
    log_lines: list[str] = []
    for i, line in enumerate(body_lines):
        if line.strip() == "log:":
            log_lines = body_lines[i + 1:]
            body_lines = body_lines[:i]
            break
    raw = "\n".join(body_lines).strip()
    payload: object = None
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
    return {
        "id": header.get("id", ""),
        "status": header.get("status", "unknown"),
        "payload": payload,
        "log": log_lines,
    }


def op_source(name: str, args: dict | None = None) -> str:
    return f"return agent_op({json.dumps(name)}, {lua_literal(args or {})})\n"


def lua_literal(value) -> str:
    """Render a Python value as Lua source (tables for dict/list)."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        parts = [f"[{lua_literal(k)}] = {lua_literal(v)}" for k, v in value.items()]
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        return "{" + ", ".join(lua_literal(v) for v in value) + "}"
    raise TypeError(f"cannot convert {type(value).__name__} to Lua")


def cmd_run(args: argparse.Namespace) -> int:
    if args.file == "-":
        source = sys.stdin.read()
    else:
        with open(args.file, encoding="utf-8") as fh:
            source = fh.read()
    return _report(run_lua(source, timeout=args.timeout))


IMPORT_OPS = {"import_media", "import_audio"}


def _track_names() -> set[str]:
    result = run_lua(op_source("tracks_list"), timeout=15)
    if result["status"] != "ok":
        raise BridgeError(f"could not list tracks: {result['payload']}")
    return {r["name"] for r in (result["payload"] or {}).get("routes", [])}


# Verified against ardour9 9.8.0 by importing one file per format and checking a
# track appeared: these are accepted, while .m4a, .aac and .raw produced nothing.
AUDIO_EXTENSIONS = {
    ".wav", ".wave", ".w64", ".rf64",          # RIFF family
    ".aif", ".aiff", ".aifc", ".au",            # AIFF / AU
    ".flac", ".ogg", ".oga", ".opus",           # lossless / Ogg family
    ".mp3", ".mp2",                             # MPEG audio
    ".caf",                                     # Core Audio
}


def _preflight(params: dict) -> list[str]:
    """Inputs Ardour cannot import at all. Checked before anything is imported."""
    paths = params.get("paths")
    if not isinstance(paths, list) or not paths:
        paths = [params.get("path")]
    problems: list[str] = []
    for path in paths:
        if not isinstance(path, str):
            continue
        try:
            fmt = smf_format(path)
        except OSError:
            continue
        if fmt == 2:
            problems.append(f"{path}: SMF Type 2 is not supported by Ardour "
                            "(its importer handles Type 0 and Type 1 only)")
    return problems


def _expected_tracks(params: dict) -> tuple[int | None, str]:
    """How many Ardour routes the import should create, and why.

    MIDI files are counted per SMF track that Ardour will keep (see bin/smf.py).
    Audio files yield one track each. Anything we cannot classify, or a merge
    import (whose grouping we have not verified), returns None: the caller must
    then report an uncertain result rather than a completed import.
    """
    if params.get("merge") is True:
        return None, "merge grouping not verified"
    paths = params.get("paths")
    if not isinstance(paths, list) or not paths:
        paths = [params.get("path")]
    total = 0
    for path in paths:
        if not isinstance(path, str):
            return None, f"unreadable input {path!r}"
        try:
            counted = model_track_count(path)
        except SmfError as exc:
            return None, f"unparsable MIDI: {exc}"
        if counted is not None:
            total += counted
            continue
        if os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS:
            total += 1
            continue
        return None, f"cannot classify {path}"
    return total, "derived from inputs"


def _finish_import(payload, result, new, expected, origin, args):
    payload.update(new_tracks=new, expected_tracks=expected, expected_source=origin,
                   waited=True, complete=len(new) >= expected)
    if expected == 0:
        payload.update(complete=False, uncertain=False,
                       warning="inputs contain no importable MIDI model events "
                               "(program change, sysex and meta do not create tracks)")
        return _report({"status": "error", "payload": payload, "log": result["log"]})
    if len(new) > expected:
        payload["uncertain"] = True
        payload["warning"] = (f"{len(new)} tracks appeared but {expected} were expected; "
                              "the extra tracks were not predicted")
        return _report({"status": "error", "payload": payload, "log": result["log"]})
    return _report({"status": "ok", "payload": payload, "log": result["log"]})


def cmd_op(args: argparse.Namespace) -> int:
    try:
        params = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        print(f"invalid JSON arguments: {exc}", file=sys.stderr)
        return 2

    if args.name not in IMPORT_OPS or args.no_wait or params.get("dry_run") is True:
        return _report(run_lua(op_source(args.name, params), timeout=args.timeout))

    problems = _preflight(params)
    if problems:
        return _report({"status": "error", "payload": {"ok": False, "unsupported": problems},
                        "log": []})

    # Imports run on worker threads. A new-track import is complete when the
    # new route appears — verified: track, region and notes become visible in
    # the same instant. Importing into the selected track creates no route, so
    # it cannot be observed from Lua and is reported as such.
    before = _track_names()
    result = run_lua(op_source(args.name, params), timeout=args.timeout)
    payload = result["payload"]
    if result["status"] != "ok" or not isinstance(payload, dict) or not payload.get("ok"):
        return _report(result)
    if payload.get("mode") == "as_track":
        expected, origin = _expected_tracks(params)
        if expected == 0:
            # Nothing in these inputs creates an Ardour track; do not wait.
            return _finish_import(payload, result, [], 0, origin, args)
        settle = 0.35
        deadline = time.monotonic() + args.timeout
        current: list[str] = []
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            new = sorted(_track_names() - before)
            if new != current:
                current, stable_since = new, time.monotonic()
                continue
            settled = stable_since is not None and time.monotonic() - stable_since >= settle
            if settled and expected is not None and len(new) >= expected:
                return _finish_import(payload, result, new, expected, origin, args)
            if settled and expected is None:
                # No trustworthy expected count. Do not claim success.
                hint = ""
                if not new:
                    hint = (" no tracks appeared at all; this format may be unsupported "
                            "by the installed Ardour build")
                payload.update(new_tracks=new, expected_tracks=None,
                               expected_source=origin, complete=False, uncertain=True,
                               warning="completion could not be verified: expected track "
                                       "count is unknown for these inputs." + hint)
                return _report({"status": "error", "payload": payload, "log": result["log"]})
            time.sleep(0.1)
        payload.update(new_tracks=current, expected_tracks=expected, expected_source=origin,
                       wait_timeout=True, complete=False)
        return _report({"status": "error", "payload": payload, "log": result["log"]})
    payload["wait_note"] = ("to_track imports create no new route, so completion cannot be "
                            "observed from Lua; verify with the native MCP track_get_regions")
    return _report(result)


def cmd_ops(_: argparse.Namespace) -> int:
    result = run_lua('return agent_op("list_ops", {})\n')
    if result["status"] != "ok":
        return _report(result)
    ops = (result["payload"] or {}).get("ops", [])
    print("\n".join(ops))
    return 0


def _report(result: dict) -> int:
    if result["status"] == "ok":
        print(json.dumps(result["payload"], indent=2, ensure_ascii=False))
        for line in result["log"]:
            print(line, file=sys.stderr)
        return 0
    payload = result["payload"]
    message = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def cmd_doctor(_: argparse.Namespace) -> int:
    """Two concerns, deliberately separate:

    * the timer hook is the PRIMARY execution path — its round-trip is a
      pass/fail check;
    * OSC is optional (direct OSC control, and the fast path where upstream's
      action bug does not apply) — reported as information, never as failure.
    """
    ok = True
    checks: list[tuple[bool, str]] = []

    bridge_file = os.path.join(SCRIPTS_DIR, "ardour_agent_bridge.lua")
    checks.append((os.path.isfile(bridge_file), f"bridge script installed: {bridge_file}"))
    checks.append((os.path.isfile(os.path.join(DATA_DIR, "agent_core.lua")),
                   f"core installed: {os.path.join(DATA_DIR, 'agent_core.lua')}"))
    checks.append((os.path.isfile(os.path.join(DATA_DIR, "ardour_ops.lua")),
                   f"ops installed: {os.path.join(DATA_DIR, 'ardour_ops.lua')}"))
    checks.append((_ardour_running(), "Ardour process running"))
    checks.append((_hook_bound(), "timer hook bound in ui_scripts (Ardour Agent Timer)"))

    info: list[tuple[bool, str]] = []
    info.append((_osc_port_listening(PORT),
                 f"OSC control surface on UDP {PORT} (optional: direct OSC control + fast path)"))

    if all(passed for passed, _ in checks):
        try:
            result = run_lua('return agent_op("ping", {})\n', timeout=8)
            passed = result["status"] == "ok"
            checks.append((passed, "timer-hook round-trip (primary execution path)"))
            if passed:
                payload = result["payload"] or {}
                print(f"session: {payload.get('session')}  sr: {payload.get('sample_rate')}")
            else:
                print(f"hook replied with error: {result['payload']}")
        except BridgeError as exc:
            checks.append((False, f"timer-hook round-trip: {str(exc).splitlines()[0]}"))

    for passed, label in checks:
        print(f"[{'ok' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    for passed, label in info:
        print(f"[{'ok' if passed else 'info'}] {label}")

    if not ok:
        print("\nTroubleshooting: docs/troubleshooting.md (or README 'Troubleshooting').")
    return 0 if ok else 1


def _hook_bound() -> bool:
    """True when ui_scripts carries our LuaCallback binding."""
    import xml.etree.ElementTree as ET

    path = os.path.join(os.path.dirname(SCRIPTS_DIR.rstrip("/")), "ui_scripts")
    try:
        root = ET.parse(path).getroot()
        hooks = root.find("ActionHooks")
        if hooks is None:
            return False
        return any(cb.get("name") == "Ardour Agent Timer" for cb in hooks.findall("LuaCallback"))
    except Exception:  # noqa: BLE001 - unreadable or malformed means "not bound"
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ardour_agent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for a reply")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ping", help="check the bridge answers and print session identity")
    p.set_defaults(func=lambda a: _report(run_lua('return agent_op("ping", {})\n', timeout=a.timeout)))

    p = sub.add_parser("run", help="run a Lua file (or - for stdin) inside Ardour")
    p.add_argument("file")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("op", help="run a registered operation")
    p.add_argument("name")
    p.add_argument("args", nargs="?", help="JSON object of arguments")
    p.add_argument("--no-wait", action="store_true",
                   help="return immediately for imports instead of waiting for the new track")
    p.set_defaults(func=cmd_op)

    p = sub.add_parser("ops", help="list registered operations")
    p.set_defaults(func=cmd_ops)

    p = sub.add_parser("doctor", help="diagnose installation, OSC and bridge")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    if hasattr(args, "timeout") is False:
        args.timeout = 15.0
    try:
        return args.func(args)
    except BridgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
