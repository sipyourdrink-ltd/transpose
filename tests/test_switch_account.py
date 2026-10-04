"""End-to-end runs of scripts/switch_account.py on a temp data folder: python3 -m pytest tests -q

HOME and ACCOUNT_SESSIONS_ROOT both point into the temp folder, and --no-app keeps the real app untouched.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "switch_account.py"
OLD, NEW, ORG = "acct-old", "acct-new", "org-1"


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def record(sid, title, activity):
    return {"sessionId": sid, "title": title, "lastActivityAt": activity, "emailAddress": "person@example.com"}


class Switch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.home, self.root = t / "home", t / "app"
        self.src = self.root / "claude-code-sessions" / OLD / ORG
        self.dst = self.root / "claude-code-sessions" / NEW / ORG
        write(self.src / "local_s1.json", record("local_s1", "from old", 100))
        write(self.dst / "local_s2.json", record("local_s2", "already new", 100))
        write(self.root / "local-agent-mode-sessions" / OLD / ORG / "local_c1.json", record("local_c1", "cowork", 1))
        write(self.root / "config.json", {"lastKnownAccountUuid": NEW, "tokenCache": "TOKEN-VALUE"})
        write(self.home / ".claude.json", {"oauthAccount": {"accountUuid": OLD, "emailAddress": "person@example.com"}})
        write(self.root / "claude_desktop_config.json", {"preferences": {"epitaxyPrefs": {"dframe-group-scopes": {
            f"{OLD}/{ORG}": {"groups": [{"id": "g1", "name": "WIP"}], "assignments": {"code:local_s1": "g1"},
                             "order": {"g1": ["code:local_s1"]}}}}}})
        self.env = dict(os.environ, HOME=str(self.home), ACCOUNT_SESSIONS_ROOT=str(self.root),
                        ACCOUNT_SESSIONS_BACKUPS=str(t / "backups"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=self.env)
        out = p.stdout + p.stderr
        self.assertNotIn("person@example.com", out)
        self.assertNotIn("TOKEN-VALUE", out)
        return p, out

    def test_stops_without_email_when_not_switched(self):
        write(self.home / ".claude.json", {"oauthAccount": {"accountUuid": NEW, "emailAddress": "person@example.com"}})
        p, out = self.run_("--check")
        self.assertEqual(p.returncode, 2, out)
        self.assertIn("STOP", out)
        self.assertIn(NEW, out)

    def test_check_is_read_only(self):
        p, out = self.run_("--check")
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("local_s1", out)
        self.assertIn("cowork: the target folder does not exist yet", out)
        self.assertFalse((self.dst / "local_s1.json").exists())
        self.assertFalse((self.root / "local-agent-mode-sessions" / NEW).exists())

    def test_check_reports_what_the_switch_will_do(self):
        write(self.src / "local_s2.json", record("local_s2", "newer in old", 200))
        p, out = self.run_("--check")
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("(take newer: 1)", out)
        self.assertLess(out.index("Target (app signed in to)"), out.index("DRY RUN"))

    def test_apply_moves_sessions_and_groups(self):
        p, out = self.run_("--yes", "--no-app")
        self.assertEqual(p.returncode, 0, out)
        self.assertTrue((self.dst / "local_s1.json").exists())
        self.assertTrue((self.root / "local-agent-mode-sessions" / NEW / ORG / "local_c1.json").exists())
        self.assertTrue((self.src / "local_s1.json").exists())  # source untouched
        cfg = json.loads((self.root / "claude_desktop_config.json").read_text())
        scope = cfg["preferences"]["epitaxyPrefs"]["dframe-group-scopes"][f"{NEW}/{ORG}"]
        self.assertEqual(scope["assignments"], {"code:local_s1": "g1"})
        self.assertEqual(len(list(self.root.glob("claude_desktop_config.json.bak-switch-*"))), 1)

    def test_apply_without_desktop_config(self):
        (self.root / "claude_desktop_config.json").unlink()
        p, out = self.run_("--yes", "--no-app")
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("nothing to merge", out)
        self.assertTrue((self.dst / "local_s1.json").exists())


if __name__ == "__main__":
    unittest.main()
