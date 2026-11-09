#!/usr/bin/env python3
"""gantry's shell-command guard for Codex and Pi: allow, ask or deny, from permissions.json.

    guard.py --codex   Codex PreToolUse hook. Prints a deny decision for a command `deny`
                       names and nothing otherwise; Codex's sandbox and approvals decide the rest.
    guard.py --pi      For gantry-guard.ts. Reads {"command": "..."}; prints
                       {"decision": "allow" | "ask" | "deny", "reason": "..."}.

The rules are ../permissions.json beside this file's directory (~/.gantry/permissions.json when
installed). The matching is permissions.py. Any failure exits 2, which Codex reads as a block
and gantry-guard.ts treats as one.
"""

import json
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from permissions import classify, load  # noqa: E402  pylint: disable=wrong-import-position

RULES = HERE.parent / "permissions.json"


def command_of(event):
    """The shell text in a Codex event (`tool_input.command`) or a Pi request (`command`)."""
    command = event.get("tool_input", event)["command"]
    if isinstance(command, list):
        if len(command) == 3 and command[1] in ("-c", "-lc"):
            return command[2]
        return shlex.join(command)
    if not isinstance(command, str):
        raise TypeError(f"command is {type(command).__name__}, not text")
    return command


def main(argv):
    mode = argv[1] if len(argv) == 2 else ""
    if mode not in ("--codex", "--pi"):
        print("usage: guard.py --codex | --pi  (event JSON on stdin)", file=sys.stderr)
        return 2
    try:
        rules = load(RULES)
        decision, reason = classify(command_of(json.load(sys.stdin)),
                                    rules["allow"], rules["ask"], rules["deny"])
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"gantry guard: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if mode == "--pi":
        print(json.dumps({"decision": decision, "reason": reason}))
    elif decision == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"gantry: {reason}",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
