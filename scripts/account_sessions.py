#!/usr/bin/env python3
"""account_sessions.py - census and merge of the Claude desktop app's per-account session lists.

The app keeps one sidebar list per account/org pair: a folder of small pointer records. The transcripts
those records point at live under ~/.claude and are shared by every account, so carrying sessions to
another account is a copy of pointer records from one folder into another. Transcripts are never touched.

    account_sessions.py card [--json] [--tree code|cowork]
        Read-only census of every <acct>/<org> folder. ANOMALIES first (app running, diverged records,
        record-vs-tombstone conflicts, pairs not yet merged), then the pair table.

    account_sessions.py merge --from ACCT/ORG --to ACCT/ORG [--tree code|cowork] [--prefer target|newer] [--apply]
        Union the source folder into the target folder. DRY RUN unless --apply. With --apply it refuses
        while the app runs and writes a .tgz backup of the target folder before any change.

Trees: code (claude-code-sessions, the Code tab, default) and cowork (local-agent-mode-sessions).
Files are copied, never linked; the source folder is never modified.
config.json (the app's token cache) is never opened by this script.
Nothing from a record is printed except its id, title, timestamps and counts.

Environment:
  ACCOUNT_SESSIONS_ROOT     app data folder (default ~/Library/Application Support/Claude)
  ACCOUNT_SESSIONS_BACKUPS  backup folder (default ~/Backups/claude-desktop)
  ACCOUNT_SESSIONS_APP_CLOSED=1 / ACCOUNT_SESSIONS_APP_RUNNING=1  override the app-running check (tests)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(os.environ.get("ACCOUNT_SESSIONS_ROOT", str(Path.home() / "Library/Application Support/Claude")))
BACKUPS = Path(os.environ.get("ACCOUNT_SESSIONS_BACKUPS", str(Path.home() / "Backups/claude-desktop")))
TREES = {"code": "claude-code-sessions", "cowork": "local-agent-mode-sessions"}
IDX = "archived-sessions.idx"
BACKLOG = Path("backlog") / "tasks.json"
SCHED = "scheduled-tasks.json"
COWORK_DIRS = ("rpm", "agent", ".project-cache")
ANOM = "  ❌ "


# ---------- model ----------

def app_running() -> bool:
    if os.environ.get("ACCOUNT_SESSIONS_APP_CLOSED"):
        return False
    if os.environ.get("ACCOUNT_SESSIONS_APP_RUNNING"):
        return True
    try:
        out = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True).stdout
    except OSError:
        return False
    return any(line.strip().endswith("Claude.app/Contents/MacOS/Claude") for line in out.splitlines())


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def pairs(tree: str) -> list[Path]:
    base = ROOT / TREES[tree]
    if not base.is_dir():
        return []
    out = []
    for acct in sorted(base.iterdir()):
        if not acct.is_dir() or acct.name.startswith("."):
            continue
        for org in sorted(acct.iterdir()):
            if org.is_dir() and not org.name.startswith("."):
                out.append(org)
    return out


def pair_label(p: Path) -> str:
    return f"{p.parent.name}/{p.name}"


def short(p: Path) -> str:
    return f"{p.parent.name[:8]}…/{p.name[:8]}…" if len(p.parent.name) > 12 else pair_label(p)


def records(folder: Path) -> dict[str, Path]:
    return {p.stem: p for p in folder.glob("local_*.json") if p.is_file()}


def tombstones(folder: Path) -> dict[str, Path]:
    return {p.name[len("deleted_"):]: p for p in folder.glob("deleted_*") if p.is_file()}


def rec_meta(p: Path) -> dict:
    """The only record fields this tool ever reads for display: title, last activity, archived flag."""
    d = load_json(p, {})
    if not isinstance(d, dict):
        d = {}
    return {"title": str(d.get("title", ""))[:60], "lastActivityAt": int(d.get("lastActivityAt") or 0),
            "isArchived": bool(d.get("isArchived", False))}


def newest_mtime(folder: Path) -> str:
    ts = [p.stat().st_mtime for p in folder.glob("local_*.json")]
    return dt.datetime.fromtimestamp(max(ts)).strftime("%Y-%m-%d") if ts else "—"


def diff(src: Path, dst: Path) -> dict:
    """Everything a merge would do, computed read-only."""
    rs, rd = records(src), records(dst)
    ts, td = tombstones(src), tombstones(dst)
    copy, same, diverged, conflicts = [], [], [], []
    for sid, sp in sorted(rs.items()):
        bare = sid[len("local_"):]
        if bare in td:
            conflicts.append((sid, "record in source, tombstone in target"))
            continue
        if sid not in rd:
            copy.append(sid)
        elif sha(sp) == sha(rd[sid]):
            same.append(sid)
        else:
            ms, md = rec_meta(sp), rec_meta(rd[sid])
            diverged.append((sid, ms["lastActivityAt"], md["lastActivityAt"], ms["title"]))
    for bare in sorted(ts):
        if "local_" + bare in rd:
            conflicts.append(("local_" + bare, "tombstone in source, record in target"))
    new_tombs = sorted(b for b in ts if b not in td and "local_" + b not in rd)
    idx_s = set(load_json(src / IDX, {}).get("archived", []) or [])
    idx_d = set(load_json(dst / IDX, {}).get("archived", []) or [])
    idx_add = sorted(idx_s - idx_d)
    bl_s = load_json(src / BACKLOG, {}).get("items", []) or []
    bl_d = load_json(dst / BACKLOG, {}).get("items", []) or []
    seen = {i.get("id") for i in bl_d if isinstance(i, dict)}
    bl_add = [i for i in bl_s if isinstance(i, dict) and i.get("id") not in seen]
    sched = "copy" if (src / SCHED).is_file() and not (dst / SCHED).is_file() else \
            ("manual" if (src / SCHED).is_file() and (dst / SCHED).is_file() and sha(src / SCHED) != sha(dst / SCHED) else "none")
    dirs = [d.name for d in src.iterdir() if d.is_dir() and d.name.startswith("local_") and not (dst / d.name).exists()]
    dirs += [n for n in COWORK_DIRS if (src / n).is_dir()]
    return {"copy": copy, "same": same, "diverged": diverged, "conflicts": conflicts, "tombstones": new_tombs,
            "idx_add": idx_add, "backlog_add": bl_add, "sched": sched, "dirs": dirs}


# ---------- card ----------

def cmd_card(a):
    trees = [a.tree] if a.tree else list(TREES)
    report = {"root": str(ROOT), "app_running": app_running(), "pairs": [], "anomalies": []}
    anomalies = report["anomalies"]
    if report["app_running"]:
        anomalies.append("app is running: quit it (Cmd+Q) before any merge; the signed-in folder is rewritten on focus")
    for tree in trees:
        ps = [p for p in pairs(tree) if records(p) or tombstones(p)]
        activity = {}
        for p in ps:
            rs = records(p)
            activity[p] = max((rec_meta(x)["lastActivityAt"] for x in rs.values()), default=0)
            report["pairs"].append({"tree": tree, "pair": pair_label(p), "records": len(rs),
                                    "archived": sum(rec_meta(x)["isArchived"] for x in rs.values()),
                                    "tombstones": len(tombstones(p)), "newest": newest_mtime(p),
                                    "canonical": False})
        if not ps:
            continue
        # the most recently active pair is the likely signed-in one and the natural merge target
        canon = max(ps, key=lambda p: activity[p])
        for r in report["pairs"]:
            if r["tree"] == tree and r["pair"] == pair_label(canon):
                r["canonical"] = True
        for src in ps:
            if src is canon:
                continue
            d = diff(src, canon)
            for sid, why in d["conflicts"]:
                anomalies.append(f"[{tree}] {sid}: {why} ({short(src)} → {short(canon)}): decide by hand, merge skips it")
            moved = [(sid, ts_, td_, t) for sid, ts_, td_, t in d["diverged"] if ts_ != td_]
            for sid, ts_, td_, title in moved:
                anomalies.append(f"[{tree}] {sid} diverged: source {ts_} vs target {td_} ({short(src)} → {short(canon)}) "
                                 f"'{title}': `--prefer newer` takes the larger lastActivityAt")
            quiet = len(d["diverged"]) - len(moved)
            if quiet:
                anomalies.append(f"[{tree}] {quiet} records differ from {short(canon)} only in settings (same lastActivityAt) "
                                 f"in {short(src)}: target kept, nothing to decide")
            if d["copy"]:
                anomalies.append(f"[{tree}] {len(d['copy'])} records only in {short(src)}, absent from {short(canon)}: "
                                 f"`merge --from {pair_label(src)} --to {pair_label(canon)}`")
    if not report["pairs"]:
        anomalies.append(f"no session folders under {ROOT}: wrong root? (ACCOUNT_SESSIONS_ROOT)")
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    print("ANOMALIES")
    print("\n".join(ANOM + x for x in anomalies) if anomalies else "  ✅ none")
    print(f"\napp: {'running' if report['app_running'] else 'closed'} · root: {ROOT}")
    print("\n| tree | acct/org | records | archived | tombstones | newest | target? |")
    print("|---|---|---|---|---|---|---|")
    for r in report["pairs"]:
        print(f"| {r['tree']} | {r['pair']} | {r['records']} | {r['archived']} | {r['tombstones']} | {r['newest']} | "
              f"{'← canonical (most recent activity)' if r['canonical'] else ''} |")
    print("\nShared by every account, not merged: ~/.claude (transcripts) · config.json (token cache) · "
          "server-side connectors, routines and pairings")
    return 0


# ---------- merge ----------

def resolve_pair(tree: str, label: str) -> Path:
    p = ROOT / TREES[tree] / Path(label)
    if len(Path(label).parts) != 2 or not p.is_dir():
        sys.exit(f"unknown pair {label!r} for tree {tree}; run `card` for the list")
    return p


def backup(dst: Path, tree: str) -> Path:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = BACKUPS / f"{TREES[tree]}-{dst.parent.name[:8]}-{dst.name[:8]}-{stamp}.tgz"
    with tarfile.open(out, "w:gz") as t:
        t.add(dst, arcname=pair_label(dst))
    return out


def copy_tree_no_clobber(src: Path, dst: Path) -> int:
    n = 0
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        q = dst / rel
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        elif not q.exists():
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, q)
            n += 1
    return n


def cmd_merge(a):
    tree = a.tree or "code"
    src, dst = resolve_pair(tree, a.src), resolve_pair(tree, a.dst)
    if src == dst:
        sys.exit("source and target are the same folder")
    d = diff(src, dst)
    take_newer = [sid for sid, ts_, td_, _ in d["diverged"] if a.prefer == "newer" and ts_ > td_]
    mode = "APPLY" if a.apply else "DRY RUN"
    print(f"{mode} · {tree} · {pair_label(src)} → {pair_label(dst)}")
    print(f"  copy {len(d['copy'])} · identical {len(d['same'])} · diverged {len(d['diverged'])} "
          f"(take newer: {len(take_newer)}) · tombstones +{len(d['tombstones'])} · idx +{len(d['idx_add'])} · "
          f"backlog +{len(d['backlog_add'])} · scheduled-tasks: {d['sched']} · dirs {len(d['dirs'])}")
    for sid in d["copy"]:
        print(f"  + {sid}  '{rec_meta(src / (sid + '.json'))['title']}'")
    for sid, ts_, td_, title in d["diverged"]:
        print(f"  ~ {sid} diverged ({ts_} vs {td_}) '{title}' → {'source' if sid in take_newer else 'target kept'}")
    for sid, why in d["conflicts"]:
        print(f"  CONFLICT {sid}: {why}; untouched")
    if d["sched"] == "manual":
        print(f"  {SCHED} differs on both sides: merge by hand (nested object, not a list)")
    if not a.apply:
        print("\nnothing written; add --apply (with the app quit) to perform the union")
        return 0
    if app_running():
        sys.exit("refusing: the Claude app is running (Cmd+Q, then rerun); its folder is rewritten on focus")
    bk = backup(dst, tree)
    print(f"\nbackup: {bk}")
    for sid in d["copy"]:
        shutil.copy2(src / (sid + ".json"), dst / (sid + ".json"))
    for sid in take_newer:
        shutil.copy2(src / (sid + ".json"), dst / (sid + ".json"))
    for bare in d["tombstones"]:
        shutil.copy2(src / ("deleted_" + bare), dst / ("deleted_" + bare))
    if d["idx_add"] or not (dst / IDX).exists():
        cur = load_json(dst / IDX, {}) or {}
        arch = sorted(set(cur.get("archived", []) or []) | set(d["idx_add"]))
        (dst / IDX).write_text(json.dumps({"v": cur.get("v", 1), "archived": arch}), encoding="utf-8")
    if d["backlog_add"]:
        cur = load_json(dst / BACKLOG, {"version": 1, "items": []}) or {"version": 1, "items": []}
        cur["items"] = list(cur.get("items", []) or []) + d["backlog_add"]
        (dst / BACKLOG).parent.mkdir(parents=True, exist_ok=True)
        (dst / BACKLOG).write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    if d["sched"] == "copy":
        shutil.copy2(src / SCHED, dst / SCHED)
    copied_files = sum(copy_tree_no_clobber(src / n, dst / n) for n in d["dirs"])
    print(f"done: {len(d['copy']) + len(take_newer)} records, {len(d['tombstones'])} tombstones, "
          f"{len(d['idx_add'])} idx ids, {len(d['backlog_add'])} backlog items, {copied_files} files in dirs")
    print("next: restart the app (Cmd+Q, reopen), then `account_sessions.py card`: expect 0 new conflicts")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("card", help="read-only census of every account/org folder")
    c.add_argument("--json", action="store_true", help="print the census as JSON")
    c.add_argument("--tree", choices=list(TREES), help="only this tree (default: both)")
    c.set_defaults(fn=cmd_card)
    m = sub.add_parser("merge", help="union one account/org folder into another (dry run unless --apply)")
    m.add_argument("--from", dest="src", required=True, metavar="ACCT/ORG", help="source folder, as `card` prints it")
    m.add_argument("--to", dest="dst", required=True, metavar="ACCT/ORG", help="target folder, as `card` prints it")
    m.add_argument("--tree", choices=list(TREES), help="code (default) or cowork")
    m.add_argument("--prefer", choices=["target", "newer"], default="target",
                   help="for a record that differs on both sides: keep the target (default) or take the one "
                        "with the larger lastActivityAt")
    m.add_argument("--apply", action="store_true", help="write the union (refuses while the app runs; backs up first)")
    m.set_defaults(fn=cmd_merge)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
