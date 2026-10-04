#!/usr/bin/env python3
"""switch_account.py - move the Code-tab sidebar (sessions and sidebar groups) to the account the
Claude desktop app is signed in to now.

Flow: in the Claude app sign out, sign in to the other account, open the Code tab once, then run this.

  switch_account.py            check, ask for Enter, quit the app, merge, merge sidebar groups, reopen the app
  switch_account.py --check    read-only: print what would happen (dry-run merges), change nothing

Target: the account the app signed in to last (`lastKnownAccountUuid`, the only key read from the app's
config.json) and that account's most recently used org folder.
Source: the account the Claude Code CLI is logged in to (`oauthAccount.accountUuid` in ~/.claude.json),
or --from-account. If the target is that same account, the app has not been switched yet and the
script stops (--allow-same to merge anyway).

The source account's folders are merged into the target with `account_sessions.py merge --apply
--prefer newer`, for both trees (code and cowork): records are copied, never linked or deleted, and a
record present on both sides is replaced only when the source has the larger lastActivityAt.
Sidebar groups live per account in claude_desktop_config.json (backed up first) and are merged into the
target's scope: the source account's groups and assignments win over the target's, groups match by id
and then by name, and sessions started by a scheduled task are never put in a group.
Pins are global in the app and need nothing.

Environment: ACCOUNT_SESSIONS_ROOT (default ~/Library/Application Support/Claude).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP = Path(os.environ.get("ACCOUNT_SESSIONS_ROOT", str(Path.home() / "Library/Application Support/Claude")))
HERE = Path(__file__).resolve().parent
SESSIONS = HERE / "account_sessions.py"
GROUPS = ("preferences", "epitaxyPrefs", "dframe-group-scopes")
TREE_DIRS = {"code": "claude-code-sessions", "cowork": "local-agent-mode-sessions"}


def app_running() -> bool:
    return subprocess.run(["pgrep", "-x", "Claude"], capture_output=True).returncode == 0


def newest_record(folder: Path) -> float:
    times = [p.stat().st_mtime for p in folder.glob("local_*.json")]
    return max(times) if times else 0.0


def account_folders(tree: str) -> list[str]:
    root = APP / TREE_DIRS[tree]
    return sorted(f"{a.name}/{o.name}" for a in root.glob("*") if a.is_dir() for o in a.glob("*") if o.is_dir())


def signed_in_account() -> str:
    """The account id the app signed in to last. Only this one key is taken from config.json."""
    try:
        acct = json.loads((APP / "config.json").read_text()).get("lastKnownAccountUuid")
    except (OSError, ValueError, AttributeError):
        return ""
    return acct if isinstance(acct, str) else ""


def target_pair() -> tuple[str, str]:
    acct = signed_in_account()
    if not acct:
        return "", ""
    orgs = sorted((APP / TREE_DIRS["code"] / acct).glob("*"), key=newest_record, reverse=True)
    return acct, (orgs[0].name if orgs else "")


def cli_account() -> str:
    """The account id the Claude Code CLI is logged in to. Only `oauthAccount.accountUuid` is taken."""
    try:
        oa = json.loads((Path.home() / ".claude.json").read_text()).get("oauthAccount") or {}
        acct = oa.get("accountUuid", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return acct if isinstance(acct, str) else ""


def routine_sessions(folder: Path) -> set[str]:
    """Return sessionIds of every local_*.json in *folder* whose record has a truthy scheduledTaskId."""
    result: set[str] = set()
    if not folder.is_dir():
        return result
    for p in folder.glob("local_*.json"):
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if rec.get("scheduledTaskId"):
            sid = rec.get("sessionId")
            if sid:
                result.add(sid)
    return result


def merge_groups(cfg: dict, target: str, first: str = "", only_first: bool = False,
                 routine: frozenset[str] | set[str] = frozenset()) -> tuple[int, int]:
    """Merge sidebar-group scopes into *target*: the *first* account wins, other accounts (unless
    *only_first*) only fill gaps, groups match by id then by name, scheduled-task sessions are never grouped.
    Returns (groups added, assignments changed)."""
    node = cfg
    for k in GROUPS:
        node = node.setdefault(k, {})
    dst = node.setdefault(target, {})
    dst.setdefault("groups", []); dst.setdefault("assignments", {}); dst.setdefault("order", {})
    rk = {"code:" + s for s in routine}
    orig_gids = {g["id"] for g in dst["groups"]}; orig_names = {g["name"] for g in dst["groups"] if "name" in g}
    orig_asgn = dict(dst["assignments"]); added_g = 0
    sources = [(True, src) for sc, src in node.items() if sc != target and first and sc.startswith(first + "/")]
    if not only_first:
        sources += [(False, src) for sc, src in node.items() if sc != target and not (first and sc.startswith(first + "/"))]
    for win, src in sources:
        n2i = {g["name"]: g["id"] for g in dst["groups"] if "name" in g}
        hids = {g["id"] for g in dst["groups"]}
        imap = {sg["id"]: n2i[sg["name"]] for sg in src.get("groups", []) if sg["id"] not in hids and sg.get("name") in n2i}
        rm = lambda gid: imap.get(gid, gid)
        for sg in src.get("groups", []):
            eid = rm(sg["id"]); idx = next((i for i, g in enumerate(dst["groups"]) if g["id"] == eid), None)
            if idx is not None:
                if win: dst["groups"][idx] = {**sg, "id": eid}
            else:
                dst["groups"].append({**sg, "id": eid})
                if eid not in orig_gids and sg.get("name") not in orig_names: added_g += 1
        for sess, gid in (src.get("assignments") or {}).items():
            if win or sess not in dst["assignments"]: dst["assignments"][sess] = rm(gid)
        for gid, sessions in (src.get("order") or {}).items():
            rgid = rm(gid)
            if win:
                seen = set(sessions); ext = [s for s in dst["order"].get(rgid, []) if s not in seen]
                dst["order"][rgid] = list(sessions) + ext
            elif rgid in {g["id"] for g in dst["groups"]} and rgid not in dst["order"]:
                dst["order"][rgid] = list(sessions)
    for s in rk: dst["assignments"].pop(s, None)
    for gid in list(dst["order"]): dst["order"][gid] = [s for s in dst["order"][gid] if s not in rk]
    for sess, gid in list(dst["assignments"].items()):
        for og in list(dst["order"]):
            if og != gid and sess in dst["order"][og]: dst["order"][og] = [s for s in dst["order"][og] if s != sess]
    for g in dst["groups"]: dst["order"].setdefault(g["id"], [])
    return added_g, sum(1 for s, g in dst["assignments"].items() if orig_asgn.get(s) != g)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="read-only: print the dry-run merges, change nothing")
    ap.add_argument("--yes", action="store_true", help="do not wait for Enter before quitting the app")
    ap.add_argument("--allow-same", action="store_true",
                    help="merge even if the app is still signed in to the CLI's account")
    ap.add_argument("--from-account", default="", metavar="ACCT",
                    help="account id to copy from (default: the account the Claude Code CLI is logged in to)")
    ap.add_argument("--all-accounts", action="store_true",
                    help="copy from every other account folder, not only the source account")
    ap.add_argument("--no-app", action="store_true",
                    help="do not quit or reopen the app (for a simulation on a copied data folder)")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with the child's when piped

    acct, org = target_pair()
    cli_acct = cli_account()
    if not acct:
        print("config.json has no lastKnownAccountUuid: open the Claude app and sign in first."); return 1
    if not org:
        print(f"Account {acct} has no Code-tab folder yet: open the Code tab once in the app, then rerun."); return 1
    target = f"{acct}/{org}"
    print(f"Target (app signed in to): {target}")
    if acct == cli_acct and not a.allow_same:
        print(f"STOP: the app is still signed in to the CLI's account {acct}. Sign out in the app, sign in to "
              f"the other account, open the Code tab once, rerun. (--allow-same to merge anyway)")
        return 2
    src_acct = a.from_account or cli_acct
    if not src_acct and not a.all_accounts:
        print("No source account: the Claude Code CLI is not logged in. Pass --from-account ACCT or --all-accounts.")
        return 1
    sources = {t: [f for f in account_folders(t) if f != target and (a.all_accounts or f.startswith(src_acct + "/"))]
               for t in TREE_DIRS}
    for t, s in sources.items():
        print(f"{t}: {len(s)} source folder(s) → target")
    if a.check:
        for t, s in sources.items():
            if s and not (APP / TREE_DIRS[t] / target).is_dir():
                print(f"{t}: the target folder does not exist yet; it will be created and the folders above copied in")
                continue
            for src in s:
                subprocess.run([sys.executable, str(SESSIONS), "merge", "--from", src, "--to", target, "--tree", t,
                                "--prefer", "newer"])
        return 0
    if not a.yes:
        input("Enter = go (the app will quit and reopen), Ctrl+C = cancel ")

    if not a.no_app:
        subprocess.run(["osascript", "-e", 'quit app "Claude"'], capture_output=True)
        for _ in range(30):
            if not app_running():
                break
            time.sleep(1)
        if app_running():
            print("Claude is still running. Quit it with Cmd+Q and run again."); return 1

    env = dict(os.environ, ACCOUNT_SESSIONS_APP_CLOSED="1") if a.no_app else None
    if sources["cowork"]:  # the new account has no cowork folder until cowork is opened once
        (APP / TREE_DIRS["cowork"] / target).mkdir(parents=True, exist_ok=True)
    rc = 0
    for t, s in sources.items():
        for src in s:
            print(f"== {t}: {src} → target")
            r = subprocess.run([sys.executable, str(SESSIONS), "merge", "--from", src, "--to", target,
                                "--tree", t, "--apply", "--prefer", "newer"], capture_output=True, text=True, env=env)
            print("\n".join((r.stdout + r.stderr).strip().splitlines()[-4:]))
            rc |= r.returncode != 0

    if rc:
        print("A merge failed: sidebar groups NOT written, app NOT reopened. Read the lines above, fix, rerun.")
        return 1
    cfg_path = APP / "claude_desktop_config.json"
    if cfg_path.is_file():
        backup = cfg_path.with_name(f"claude_desktop_config.json.bak-switch-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(cfg_path, backup)
        cfg = json.loads(cfg_path.read_text())
        routine = routine_sessions(APP / TREE_DIRS["code"] / target)
        g, asg = merge_groups(cfg, target, first=src_acct, only_first=not a.all_accounts, routine=routine)
        tmp = cfg_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        os.replace(tmp, cfg_path)
        print(f"sidebar groups: +{g} groups, +{asg} assignments into the target (backup {backup.name})")
    else:
        print("sidebar groups: no claude_desktop_config.json, nothing to merge")

    if not a.no_app:
        subprocess.run(["open", "-a", "Claude"])
    print("Done. The app is reopening." if not a.no_app else "Done.")
    subprocess.run([sys.executable, str(SESSIONS), "card"], env=env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
