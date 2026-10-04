<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sipyourdrink-ltd/transpose/main/assets/transpose-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/sipyourdrink-ltd/transpose/main/assets/transpose-light.svg">
  <img alt="transpose: two rows of session pointers, one per account; the second row is a copy of the first, and both point at the same transcripts, which stay where they are" src="https://raw.githubusercontent.com/sipyourdrink-ltd/transpose/main/assets/transpose-light.svg" width="820">
</picture>

### Carry your Claude Code desktop sessions to another account on the same Mac

[![tests](https://github.com/sipyourdrink-ltd/transpose/actions/workflows/test.yml/badge.svg)](https://github.com/sipyourdrink-ltd/transpose/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/sipyourdrink-ltd/transpose)](https://github.com/sipyourdrink-ltd/transpose/releases)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/transpose)](LICENSE)

[install](#install) &middot; [use](#use) &middot; [what is carried](#what-is-carried) &middot; [safety](#safety) &middot; [undo](#undo) &middot; [limits](#limits)

</div>

---

> **Status: unofficial.** transpose is not made or endorsed by Anthropic. It reads and writes the Claude desktop app's local data folder, whose layout is not documented and can change with an app update. Every write is preceded by a backup. macOS only; developed against Claude for Mac 2.19675.

Sign in to a different account in the Claude desktop app and the Code tab comes up empty: no sessions, no sidebar groups. Nothing was lost. The transcripts are still on disk under `~/.claude` and are shared by every account. What is per account is the sidebar: a folder of small pointer records, one per session.

transpose copies those pointers, and the sidebar groups, from the account you left into the account you are signed in to now.

### at a glance

- **Pointers are copied, transcripts are not touched.** The old account's list stays exactly as it was.
- **A dry run first.** `--check` prints every record it would copy and writes nothing.
- **A backup before every write.** The target folder goes into a `.tgz`, the app's config file into a dated copy.
- **Small enough to read.** Two Python scripts, standard library only, no network calls. [`scripts/`](scripts).
- **A menu-bar app if you switch often.** One Swift file that runs the same two scripts.

### install

```bash
git clone https://github.com/sipyourdrink-ltd/transpose
```

That is all for the command line: the scripts run on the `python3` that ships with macOS (3.9 or later).

The menu-bar app is optional and is built on your machine (needs the Xcode Command Line Tools, macOS 13 or later):

```bash
cd transpose/menubar && ./build.sh install
```

It lands in `~/Applications/Transpose.app` with the scripts inside the bundle, signed ad hoc.

### use

1. In the Claude app: sign out, sign in to the other account, open the Code tab once.
2. Look at what would happen. Nothing is written:

```bash
python3 scripts/switch_account.py --check
```

```text
Target (app signed in to): e93b07d4-…/b2a9e6c1-…
code: 1 source folder(s) → target
cowork: 0 source folder(s) → target
DRY RUN · code · 7c1e42aa-…/41d0c7f2-… → e93b07d4-…/b2a9e6c1-…
  copy 4 · identical 0 · diverged 0 (take newer: 0) · tombstones +0 · idx +0 · backlog +0 · scheduled-tasks: none · dirs 0
  + local_01a4c9e2  'Invoice rounding: integer cents'
  + local_02a4c9e2  'Flaky test in the importer'
  + local_03a4c9e2  'Release notes for 2.4'
  + local_04a4c9e2  'Migrate CI to the new runner'
```

3. Do it. The script waits for Enter, quits the app, copies, and reopens the app:

```bash
python3 scripts/switch_account.py
```

```text
== code: 7c1e42aa-…/41d0c7f2-… → target
backup: ~/Backups/claude-desktop/claude-code-sessions-e93b07d4-b2a9e6c1-20261004-193318.tgz
done: 4 records, 0 tombstones, 0 idx ids, 0 backlog items, 0 files in dirs
sidebar groups: +1 groups, +1 assignments into the target (backup claude_desktop_config.json.bak-switch-20261004-193318)
```

Both listings are output from a sample data folder, trimmed, with the ids shortened.

**Which account is the source?** The one the Claude Code CLI is logged in to. Pass `--from-account <id>` to name another, or `--all-accounts` to gather the sessions of every account into the current one. If the app is still signed in to the source account, the script stops and says so.

The menu-bar app offers the same four actions: move, check, gather from all accounts, census.

### what is carried

| carried | how |
|---|---|
| session records | copied when the target does not have them |
| a session changed on both sides | the one with the later activity wins |
| deletion markers | copied, so a session you deleted does not come back |
| archived list, backlog items | merged by id |
| scheduled tasks file | copied when the target has none; when both differ it is left for you |
| per-session folders, Cowork folders | copied without overwriting |
| sidebar groups, assignments, order | merged; groups match by id, then by name |

| not carried | why |
|---|---|
| transcripts | already shared by every account, under `~/.claude` |
| pins | global in the app |
| connectors, routines, pairings | kept on the server, per account |
| sign-in state | never read beyond one account id, never written |

Sessions started by a scheduled task are carried but are not put into a sidebar group.

### safety

| if | then |
|---|---|
| the app is running | the switch quits it and stops if it is still up after 30 seconds; `merge --apply` refuses outright |
| anything is about to be written | the target folder is archived to `~/Backups/claude-desktop/` first, and the config file is copied beside itself |
| a session exists on one side and is deleted on the other | reported as a conflict and skipped |
| a record differs on both sides | compared by the activity time inside the record, not by file date |
| one of the merges fails | sidebar groups are not written and the app is not reopened |
| the app is still on the source account | stop, exit code 2 |

The source folder is never written. Files are copied, never linked.

**What is read.** From the app's `config.json` (which also holds the sign-in tokens) one key: the id of the account signed in last. From `~/.claude.json` one key: the CLI's account id. Session records are read for their id, title, activity time and flags.

**What is printed.** Account and organisation ids, session titles (first 60 characters), counts, timestamps, and paths. No e-mail address and nothing from the token cache: the switch tests assert that on every run.

All of it is covered by 23 tests that run against temporary folders:

```bash
cd transpose && python3 -m unittest discover -s tests
```

### undo

Quit the app first. Each run names its two backups in the output.

Sessions: move the target folder aside, then unpack the archive in its place. The archive holds the folder as `<account>/<org>/`.

```bash
tar -xzf ~/Backups/claude-desktop/<archive>.tgz -C ~/Library/Application\ Support/Claude/claude-code-sessions
```

Sidebar groups: copy `claude_desktop_config.json.bak-switch-<time>` over `claude_desktop_config.json` in `~/Library/Application Support/Claude/`.

### by hand

`switch_account.py` is a wrapper. The two verbs beneath it work on any pair of folders:

```bash
python3 scripts/account_sessions.py card
```

A read-only census: every account folder with its record count, and above the table anything that needs a decision (conflicts, records that differ, folders not yet merged).

```bash
python3 scripts/account_sessions.py merge --from <acct>/<org> --to <acct>/<org>
```

A dry run of one merge. Add `--apply` to write it, `--prefer newer` to let the later activity win, `--tree cowork` for the Cowork list. `--help` on either script has the rest.

### limits

- **The layout is the app's, not ours.** An app update can move or rename things. When the folders are not where transpose expects them it finds nothing and stops; it does not guess.
- **One Mac.** It copies pointers between accounts on the same machine. It does not move transcripts to another computer.
- **Copies, not moves.** The account you left keeps its list. Sign back in and it is all still there.
- **Server-side state stays behind.** Connectors, routines and pairings belong to the account and have to be set up again.
- **Backups are full copies.** A backup holds the records as the app wrote them, which can include your account e-mail. They stay on your disk; clear `~/Backups/claude-desktop` when you like.
- **The census shows session titles and your home path.** Read it before pasting it somewhere.
- **The menu-bar app is signed ad hoc.** It is built on your machine for your machine and is not notarised.

Something off? [Open an issue](https://github.com/sipyourdrink-ltd/transpose/issues) with the output of `switch_account.py --check`, ids shortened.

### why the name?

To *transpose* a piece is to play the same music in another key. It comes from the same house as [Bernstein](https://github.com/sipyourdrink-ltd/bernstein) and [segue](https://github.com/sipyourdrink-ltd/segue).

### license

[Apache-2.0](LICENSE).
