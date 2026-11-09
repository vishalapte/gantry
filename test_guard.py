"""Tests for shared/hooks/guard.py, the shell-command guard Codex and Pi run.

Run: python3 -m unittest test_guard

The rules are the shipped shared/permissions.json. Each glob is turned into a command that
matches it, as test_compound_allow.py does, and the guard must answer deny for every deny
glob, ask for every ask glob, and allow for every allow glob no ask or deny glob covers.
"""

import json
import os
import subprocess
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GUARD = ROOT / "shared" / "hooks" / "guard.py"
RULES = json.loads((ROOT / "shared" / "permissions.json").read_text())


def sample(glob):
    """A command that matches `glob`: a trailing ` *` becomes ` x`, other `*` become `x` or go."""
    if glob.endswith(" *"):
        glob = glob[:-1] + "x"
    return glob.removesuffix("*").removeprefix("*").replace("*", "x")


def covered(command, globs):
    return any(fnmatchcase(command, g) or (g.endswith(" *") and command == g[:-2]) for g in globs)


def run(mode, event, guard=GUARD):
    return subprocess.run([str(guard), mode], input=json.dumps(event), capture_output=True,
                          text=True, check=False)


def pi(command):
    out = run("--pi", {"command": command})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["decision"]


def codex(command):
    """"deny", or None when the hook says nothing."""
    out = run("--codex", {"tool_name": "Bash", "tool_input": {"command": command}})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] if out.stdout else None


class Guard(unittest.TestCase):
    def test_every_deny_glob_is_denied(self):
        for glob in RULES["deny"]:
            command = sample(glob)
            with self.subTest(glob=glob, command=command):
                self.assertEqual(pi(command), "deny")
                self.assertEqual(codex(command), "deny")
                self.assertEqual(pi(f"ls && {command}"), "deny")

    def test_every_ask_glob_asks(self):
        for glob in RULES["ask"]:
            command = sample(glob)
            if covered(command, RULES["deny"]):
                continue
            with self.subTest(glob=glob, command=command):
                self.assertEqual(pi(command), "ask")
                self.assertIsNone(codex(command), "Codex asks through its sandbox, not the hook")

    def test_every_allow_glob_runs(self):
        for glob in RULES["allow"]:
            command = sample(glob)
            if covered(command, RULES["ask"] + RULES["deny"]):
                continue
            with self.subTest(glob=glob, command=command):
                self.assertEqual(pi(command), "allow")
                self.assertEqual(pi(f"{command} && {command}"), "allow")

    def test_deny_beats_ask_beats_allow(self):
        self.assertEqual(pi("python manage.py migrate"), "ask", "allowed `python manage.py *`, "
                         "asked `*manage.py migrate*`")
        self.assertEqual(pi("python manage.py flush"), "deny")

    def test_no_rule_asks(self):
        self.assertEqual(pi("bash x.sh"), "ask")
        self.assertEqual(pi(""), "ask")

    def test_a_command_it_cannot_read_asks_or_is_denied(self):
        self.assertEqual(pi("ls $(whoami)"), "ask")
        self.assertEqual(pi("ls 'unclosed"), "ask")
        self.assertEqual(pi("ls > out.txt"), "ask")
        self.assertEqual(pi("echo $(git push --no-verify)"), "deny")
        self.assertEqual(codex("x=$(git commit -n -m y)"), "deny")

    def test_codex_list_commands_are_read(self):
        out = run("--codex", {"tool_input": {"command": ["bash", "-lc", "git push --force"]}})
        self.assertIn('"deny"', out.stdout)

    def test_any_failure_exits_2(self):
        for mode, stdin in (("--pi", "not json"), ("--codex", "{}"), ("--bogus", "{}")):
            with self.subTest(mode=mode):
                out = subprocess.run([str(GUARD), mode], input=stdin, capture_output=True,
                                     text=True, check=False)
                self.assertEqual(out.returncode, 2)
                self.assertEqual(out.stdout, "")

    def test_the_codex_hook_command_blocks_when_the_guard_is_missing(self):
        hook = json.loads((ROOT / "codex" / "hooks.json").read_text())
        command = hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        out = subprocess.run(["sh", "-c", command], input="{}", capture_output=True, text=True,
                             env={"HOME": "/nonexistent", "PATH": os.environ["PATH"]}, check=False)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
