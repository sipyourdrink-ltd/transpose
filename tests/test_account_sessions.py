"""Tests for scripts/account_sessions.py: python3 -m pytest tests -q"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "account_sessions.py"

A, B = "acct-a", "acct-b"
ORG = "org-1"


def record(sid, title, activity, archived=False):
    return {"sessionId": sid, "cliSessionId": "cli-" + sid[6:], "cwd": "/w", "title": title,
            "isArchived": archived, "createdAt": 1, "lastActivityAt": activity, "lastFocusedAt": 1}


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.code = self.root / "claude-code-sessions"
        self.src = self.code / A / ORG
        self.dst = self.code / B / ORG
        # source: s1 (unique), s2 (identical in both), s3 (diverged, newer in source), s4 (tombstoned in target)
        write(self.src / "local_s1.json", record("local_s1", "only in a", 100))
        write(self.src / "local_s2.json", record("local_s2", "same", 100))
        write(self.src / "local_s3.json", record("local_s3", "diverged new", 300))
        write(self.src / "local_s4.json", record("local_s4", "deleted in b", 100))
        write(self.src / "archived-sessions.idx", {"v": 1, "archived": ["local_s1"]})
        write(self.src / "deleted_x", "1700000000000")
        write(self.src / "backlog" / "tasks.json", {"version": 1, "items": [{"id": "t1", "title": "a"}]})
        # target
        write(self.dst / "local_s2.json", record("local_s2", "same", 100))
        write(self.dst / "local_s3.json", record("local_s3", "diverged old", 200))
        write(self.dst / "local_s5.json", record("local_s5", "only in b", 100))
        write(self.dst / "archived-sessions.idx", {"v": 1, "archived": ["local_s5"]})
        write(self.dst / "deleted_s4", "1700000000000")
        write(self.dst / "backlog" / "tasks.json", {"version": 1, "items": [{"id": "t2", "title": "b"}]})
        write(self.root / "config.json", "{}")
        self.env = dict(os.environ, ACCOUNT_SESSIONS_ROOT=str(self.root), ACCOUNT_SESSIONS_APP_CLOSED="1",
                        ACCOUNT_SESSIONS_BACKUPS=str(self.root / "backups"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env or self.env)


class Cli(Fixture):
    def test_help_lists_card_and_merge_only(self):
        p = self.run_("--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("{card,merge}", p.stdout)


class Card(Fixture):
    def test_card_lists_pairs_and_flags_conflicts(self):
        p = self.run_("card")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout
        self.assertTrue(out.startswith("ANOMALIES"), out[:80])
        self.assertIn(f"{A}/{ORG}", out)
        self.assertIn(f"{B}/{ORG}", out)
        self.assertIn("local_s4", out)          # record vs tombstone conflict
        self.assertIn("diverged", out)          # s3 differs
        self.assertNotIn("config.json", out.split("ANOMALIES")[1].split("|")[0])

    def test_card_never_prints_record_values_beyond_title(self):
        write(self.src / "local_s9.json", dict(record("local_s9", "t", 1), emailAddress="SECRET@example.com"))
        p = self.run_("card")
        self.assertNotIn("SECRET@example.com", p.stdout)

    def test_card_json(self):
        p = self.run_("card", "--json")
        data = json.loads(p.stdout)
        self.assertIn("pairs", data)
        self.assertIn("anomalies", data)


class Merge(Fixture):
    def pair(self, acct):
        return f"{acct}/{ORG}"

    def test_dry_run_changes_nothing(self):
        before = sorted(x.name for x in self.dst.iterdir())
        p = self.run_("merge", "--from", self.pair(A), "--to", self.pair(B))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DRY RUN", p.stdout)
        self.assertIn("local_s1", p.stdout)
        self.assertEqual(before, sorted(x.name for x in self.dst.iterdir()))

    def test_refuses_while_app_running(self):
        env = dict(self.env)
        env.pop("ACCOUNT_SESSIONS_APP_CLOSED")
        env["ACCOUNT_SESSIONS_APP_RUNNING"] = "1"
        p = self.run_("merge", "--from", self.pair(A), "--to", self.pair(B), "--apply", env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.dst / "local_s1.json").exists())

    def test_apply_unions_without_clobber_and_backs_up(self):
        p = self.run_("merge", "--from", self.pair(A), "--to", self.pair(B), "--apply")
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertTrue((self.dst / "local_s1.json").exists())
        self.assertTrue((self.dst / "local_s5.json").exists())
        # diverged: target kept by default
        self.assertEqual(json.loads((self.dst / "local_s3.json").read_text())["title"], "diverged old")
        # tombstone conflict: not resurrected
        self.assertFalse((self.dst / "local_s4.json").exists())
        self.assertTrue((self.dst / "deleted_x").exists())
        idx = json.loads((self.dst / "archived-sessions.idx").read_text())
        self.assertEqual(idx["v"], 1)
        self.assertEqual(sorted(idx["archived"]), ["local_s1", "local_s5"])
        items = json.loads((self.dst / "backlog" / "tasks.json").read_text())["items"]
        self.assertEqual(sorted(i["id"] for i in items), ["t1", "t2"])
        backups = list((self.root / "backups").glob("*.tgz"))
        self.assertEqual(len(backups), 1)
        self.assertIn("CONFLICT", p.stdout)
        # source untouched
        self.assertTrue((self.src / "local_s1.json").exists())

    def test_prefer_newer_uses_record_time_not_mtime(self):
        os.utime(self.dst / "local_s3.json", None)  # target file is the newest on disk
        p = self.run_("merge", "--from", self.pair(A), "--to", self.pair(B), "--apply", "--prefer", "newer")
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(json.loads((self.dst / "local_s3.json").read_text())["title"], "diverged new")

    def test_apply_is_idempotent(self):
        self.run_("merge", "--from", self.pair(A), "--to", self.pair(B), "--apply")
        p = self.run_("merge", "--from", self.pair(A), "--to", self.pair(B))
        self.assertIn("copy 0", p.stdout)

    def test_unknown_pair_fails(self):
        p = self.run_("merge", "--from", "nope/org", "--to", self.pair(B))
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
