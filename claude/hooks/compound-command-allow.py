#!/usr/bin/env python3
"""PreToolUse hook: auto-allow a compound Bash command (`a && b`, `a | b`, `a; b`) when every
part matches an `allow` rule and no part matches a `deny` or `ask` rule.

Claude Code prompts for a compound command even when each part is allowed on its own; this
hook answers that prompt. When it is not sure, it prints nothing and Claude Code decides as it
would without the hook: prompt, block, or allow. README.md, "Auto-approving compound
commands", lists what should pass and what should not, and test_compound_allow.py holds each
example.

Rules come from ~/.claude/settings.json and the project's .claude/settings.json and
.claude/settings.local.json. The matching is gantry's one matcher, ~/.gantry/hooks/permissions.py;
when that file is missing the hook prints nothing.
"""

import json
import sys
from pathlib import Path

GANTRY_HOOKS = Path.home() / ".gantry" / "hooks"


def rules(cwd, key):
    """The Bash patterns under `permissions.<key>` in every settings file that applies."""
    found = []
    for path in (Path.home() / ".claude/settings.json", cwd / ".claude/settings.json",
                 cwd / ".claude/settings.local.json"):
        try:
            entries = json.loads(path.read_text()).get("permissions", {}).get(key, [])
        except (OSError, ValueError, AttributeError):
            continue
        for rule in entries:
            if isinstance(rule, str) and rule.startswith("Bash(") and rule.endswith(")"):
                found.append(rule[5:-1].removesuffix("*").removesuffix(":"))
    return found


def main():
    sys.path.insert(0, str(GANTRY_HOOKS))
    try:
        from permissions import matches, parts  # pylint: disable=import-outside-toplevel
    except ImportError:
        return
    try:
        event = json.load(sys.stdin)
        command = event["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return
    cwd = Path(event.get("cwd") or ".")
    allow = rules(cwd, "allow")
    stop = rules(cwd, "deny") + rules(cwd, "ask")
    split = parts(command) if allow and command else None
    if split and all(matches(p, allow) and not matches(p, stop) for p in split):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Every part of the compound command matches an allow "
                                        "rule, and none a deny or ask rule",
        }}))


if __name__ == "__main__":
    main()
