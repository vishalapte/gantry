"""Tests for ./install. Run: python3 -m unittest test_install

Every run installs into a scratch HOME, with CLAUDE_CONFIG_DIR, CODEX_HOME and
PI_CODING_AGENT_DIR inside it, so no real harness directory is touched.
"""

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATHS = ("ENINE_MAIL_IDENTITY", "ENINE_MAIL_SENDERS", "ENINE_MAIL_DRAFTS", "ENINE_MAIL_SENT")
GUARD = '"$HOME/.gantry/hooks/guard.py" --codex || exit 2'


class Install(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.claude = self.home / "claude"
        self.codex = self.home / "codex"
        self.pi = self.home / "pi"
        self.gantry = self.home / ".gantry"
        self.settings = self.claude / "settings.json"

    def run_install(self, *args, root=ROOT):
        env = dict(os.environ, HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude),
                   CODEX_HOME=str(self.codex), PI_CODING_AGENT_DIR=str(self.pi))
        return subprocess.run([str(root / "install"), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, check=False)

    def install(self, *args):
        result = self.run_install("--agent", "alice", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_the_shipped_env_holds_the_placeholder(self):
        env = json.loads((ROOT / "shared" / "env.json").read_text())
        for key in PATHS:
            self.assertIn("/<agent>/", env[key])

    def test_the_agent_name_replaces_the_placeholder_everywhere(self):
        self.install()
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings["env"]["ENINE_MAIL_IDENTITY"], "~/.config/alice/identity.json")
        self.assertEqual(settings["env"]["ENINE_MAIL_SENT"], "~/.local/share/alice/sent.jsonl")
        self.assertEqual(json.loads((self.gantry / "env.json").read_text()), settings["env"])
        codex = tomllib.loads((self.codex / "config.toml").read_text())
        self.assertEqual(codex["shell_environment_policy"]["set"], settings["env"])
        for path in (self.settings, self.gantry / "env.json", self.codex / "config.toml"):
            self.assertNotIn("<agent>", path.read_text())

    def test_settings_hold_every_shell_rule_as_bash(self):
        self.install()
        rules = json.loads(self.settings.read_text())["permissions"]
        shell = json.loads((ROOT / "shared" / "permissions.json").read_text())
        claude = json.loads((ROOT / "claude" / "settings.json").read_text())["permissions"]
        for key in ("allow", "ask", "deny"):
            self.assertEqual(rules[key], claude[key] + [f"Bash({g})" for g in shell[key]])

    def test_every_harness_reads_the_one_agents_md(self):
        self.install()
        shared = self.gantry / "AGENTS.md"
        self.assertEqual(shared.read_bytes(), (ROOT / "shared" / "AGENTS.md").read_bytes())
        self.assertEqual(os.readlink(self.codex / "AGENTS.md"), str(shared))
        self.assertEqual(os.readlink(self.pi / "AGENTS.md"), str(shared))
        self.assertIn("@~/.gantry/AGENTS.md\n", (self.claude / "CLAUDE.md").read_text())
        self.assertIn("<!-- RACECAR -->", (self.claude / "CLAUDE.md").read_text())

    def test_skills_live_in_agents_and_claude_links_them(self):
        self.install()
        for skill in (ROOT / "shared" / "skills").iterdir():
            real = self.home / ".agents" / "skills" / skill.name
            with self.subTest(skill=skill.name):
                self.assertTrue((real / "SKILL.md").is_file())
                self.assertFalse(real.is_symlink())
                self.assertEqual(os.readlink(self.claude / "skills" / skill.name), str(real))
        self.assertTrue((self.claude / "skills" / "enine-mail" / "mail.py").is_file())

    def test_git_hooks_reach_claude_through_a_link(self):
        self.install()
        self.assertEqual(os.readlink(self.claude / "git-hooks"), str(self.gantry / "git-hooks"))
        self.assertTrue((self.claude / "git-hooks" / "pre-push").is_file())

    def test_a_second_run_changes_nothing(self):
        self.install()
        self.assertIn("already match", self.install().stdout)

    def test_another_name_changes_only_the_rendered_env(self):
        self.install()
        result = self.run_install("--agent", "bob", "--dry-run")
        changed = sorted(line.split()[-1] for line in result.stdout.splitlines()
                         if "would replace" in line)
        self.assertEqual(changed, sorted(str(p) for p in (
            self.settings, self.gantry / "env.json", self.codex / "config.toml")))

    def test_no_name_without_a_terminal_is_refused(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--agent NAME", result.stderr)
        self.assertFalse(self.claude.exists())

    def test_a_bad_name_is_refused(self):
        for name in ("../x", "a/b", "A", "a b", "-x", ""):
            with self.subTest(name=name):
                self.assertEqual(self.run_install("--agent", name).returncode, 2)
                self.assertFalse(self.claude.exists())

    def test_a_dry_run_writes_nothing(self):
        self.install("--dry-run")
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [])

    def test_an_old_real_skill_dir_and_git_hooks_are_saved_then_linked(self):
        old_skill = self.claude / "skills" / "enine-mail"
        old_skill.mkdir(parents=True)
        (old_skill / "mail.py").write_text("old")
        (self.claude / "git-hooks").mkdir()
        (self.claude / "git-hooks" / "pre-push").write_text("old")
        self.install()
        self.assertTrue(old_skill.is_symlink())
        self.assertTrue((self.claude / "git-hooks").is_symlink())
        saved = list((self.gantry / "backups").rglob("mail.py"))
        self.assertEqual([p.read_text() for p in saved], ["old"])
        self.assertEqual(len(list((self.gantry / "backups").rglob("pre-push"))), 1)

    def test_codex_hooks_keep_every_other_handler(self):
        other = {"type": "command", "command": "racecar-hook.py"}
        start = {"matcher": "startup", "hooks": [{"type": "command", "command": "s.py"}]}
        self.codex.mkdir()
        (self.codex / "hooks.json").write_text(json.dumps({"hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [other]}], "SessionStart": [start]}}))
        self.install()
        hooks = json.loads((self.codex / "hooks.json").read_text())["hooks"]
        self.assertEqual(hooks["SessionStart"], [start])
        self.assertEqual(len(hooks["PreToolUse"]), 1)
        commands = [h["command"] for h in hooks["PreToolUse"][0]["hooks"]]
        self.assertEqual(commands, ["racecar-hook.py", GUARD])
        self.assertIn("already match", self.install().stdout)

    def test_codex_config_keeps_the_owners_tables(self):
        self.codex.mkdir()
        (self.codex / "config.toml").write_text('[projects."/x"]\ntrust_level = "trusted"\n')
        self.install()
        config = tomllib.loads((self.codex / "config.toml").read_text())
        self.assertEqual(config["projects"], {"/x": {"trust_level": "trusted"}})
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertEqual(config["approval_policy"], "on-request")
        self.assertIn("already match", self.install().stdout)

    def test_codex_config_setting_gantrys_keys_is_refused(self):
        for line in ('sandbox_mode = "read-only"\n', "[shell_environment_policy]\ninherit = 'all'\n"):
            with self.subTest(line=line):
                self.codex.mkdir(exist_ok=True)
                (self.codex / "config.toml").write_text(line)
                result = self.run_install("--agent", "alice")
                self.assertEqual(result.returncode, 1)
                self.assertIn("refused", result.stderr)
                self.assertFalse(self.claude.exists(), "nothing is written when one target refuses")

    def test_pi_gets_the_guard_extension(self):
        self.install()
        link = self.pi / "extensions" / "gantry-guard.ts"
        self.assertEqual(os.readlink(link), str(self.gantry / "pi" / "gantry-guard.ts"))
        self.assertEqual(link.read_bytes(), (ROOT / "pi" / "gantry-guard.ts").read_bytes())

    def test_a_pycache_directory_is_not_installed(self):
        repo = self.home / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        cache = repo / "shared" / "skills" / "enine-mail" / "__pycache__"
        cache.mkdir()
        (cache / "mail.cpython.pyc").write_text("")
        result = self.run_install("--agent", "alice", "--dry-run", root=repo)
        self.assertNotIn("__pycache__", result.stdout)
        self.run_install("--agent", "alice", root=repo)
        self.assertEqual(list(self.home.joinpath(".agents").rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
