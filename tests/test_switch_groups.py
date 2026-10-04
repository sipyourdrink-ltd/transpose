"""Sidebar-group merge in scripts/switch_account.py: python3 -m pytest tests -q"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "switch_account.py"
spec = importlib.util.spec_from_file_location("switch_account", SCRIPT)
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)

SRC, DST, OTHER = "acct-old/org", "acct-new/org", "acct-x/org"


def cfg(scopes):
    return {"preferences": {"epitaxyPrefs": {"dframe-group-scopes": scopes}}}


def scopes(c):
    return c["preferences"]["epitaxyPrefs"]["dframe-group-scopes"]


class MergeGroups(unittest.TestCase):
    def base(self):
        return cfg({
            SRC: {"groups": [{"id": "g1", "name": "IN PROGRESS"}, {"id": "g2", "name": "DONE"}],
                  "assignments": {"code:local_a": "g1", "code:local_b": "g1", "code:local_r": "g2"},
                  "order": {"g1": ["code:local_a", "code:local_b"], "g2": ["code:local_r"]}},
            DST: {"groups": [{"id": "g1", "name": "stale name"}, {"id": "g9", "name": "NEW ONLY"}],
                  "assignments": {"code:local_a": "g9", "code:local_c": "g1", "code:local_r2": "g9"},
                  "order": {"g1": ["code:local_c", "code:local_b"], "g9": ["code:local_a", "code:local_r2"]}},
            OTHER: {"groups": [{"id": "g7", "name": "OTHER"}],
                    "assignments": {"code:local_a": "g7", "code:local_d": "g7"},
                    "order": {"g7": ["code:local_a", "code:local_d"]}},
        })

    def test_source_assignment_beats_existing_target(self):
        c = self.base()
        sw.merge_groups(c, DST, first="acct-old", only_first=True)
        self.assertEqual(scopes(c)[DST]["assignments"]["code:local_a"], "g1")
        self.assertEqual(scopes(c)[DST]["assignments"]["code:local_c"], "g1")  # target-only stays

    def test_source_group_definition_and_order_win(self):
        c = self.base()
        sw.merge_groups(c, DST, first="acct-old", only_first=True)
        d = scopes(c)[DST]
        self.assertEqual({g["id"]: g["name"] for g in d["groups"]}["g1"], "IN PROGRESS")
        self.assertEqual([g["id"] for g in d["groups"]].count("g1"), 1)
        self.assertIn("g9", [g["id"] for g in d["groups"]])
        self.assertEqual(d["order"]["g1"], ["code:local_a", "code:local_b", "code:local_c"])
        self.assertNotIn("code:local_a", d["order"]["g9"])  # a moved to g1, not listed twice

    def test_routine_runs_never_grouped(self):
        c = self.base()
        sw.merge_groups(c, DST, first="acct-old", only_first=True, routine={"local_r", "local_r2"})
        d = scopes(c)[DST]
        self.assertNotIn("code:local_r", d["assignments"])
        self.assertNotIn("code:local_r2", d["assignments"])  # already in the target: removed too
        for sessions in d["order"].values():
            self.assertFalse({"code:local_r", "code:local_r2"} & set(sessions))

    def test_other_accounts_only_fill_gaps(self):
        c = self.base()
        sw.merge_groups(c, DST, first="acct-old", only_first=False)
        d = scopes(c)[DST]
        self.assertEqual(d["assignments"]["code:local_a"], "g1")
        self.assertEqual(d["assignments"]["code:local_d"], "g7")

    def test_fresh_target_scope(self):
        c = self.base()
        del scopes(c)[DST]
        g, a = sw.merge_groups(c, DST, first="acct-old", only_first=True, routine={"local_r"})
        d = scopes(c)[DST]
        self.assertEqual(d["assignments"], {"code:local_a": "g1", "code:local_b": "g1"})
        self.assertEqual(d["order"]["g2"], [])
        self.assertEqual((g, a), (2, 2))

    def test_same_name_other_id_is_the_same_group(self):
        c = cfg({
            SRC: {"groups": [{"id": "s1", "name": "On Demand", "color": "red"}],
                  "assignments": {"code:local_a": "s1"}, "order": {"s1": ["code:local_a"]}},
            DST: {"groups": [{"id": "t1", "name": "On Demand"}],
                  "assignments": {"code:local_b": "t1"}, "order": {"t1": ["code:local_b"]}},
        })
        g, _ = sw.merge_groups(c, DST, first="acct-old", only_first=True)
        d = scopes(c)[DST]
        self.assertEqual(d["groups"], [{"id": "t1", "name": "On Demand", "color": "red"}])
        self.assertEqual(d["assignments"], {"code:local_a": "t1", "code:local_b": "t1"})
        self.assertEqual(d["order"], {"t1": ["code:local_a", "code:local_b"]})
        self.assertEqual(g, 0)

    def test_idempotent(self):
        c = self.base()
        sw.merge_groups(c, DST, first="acct-old", only_first=True, routine={"local_r"})
        once = json.dumps(c, sort_keys=True)
        sw.merge_groups(c, DST, first="acct-old", only_first=True, routine={"local_r"})
        self.assertEqual(json.dumps(c, sort_keys=True), once)


class RoutineSessions(unittest.TestCase):
    def test_reads_scheduled_task_id(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            (p / "local_r.json").write_text(json.dumps({"sessionId": "local_r", "scheduledTaskId": "nightly"}))
            (p / "local_u.json").write_text(json.dumps({"sessionId": "local_u"}))
            (p / "local_bad.json").write_text("{not json")
            self.assertEqual(sw.routine_sessions(p), {"local_r"})


if __name__ == "__main__":
    unittest.main()
