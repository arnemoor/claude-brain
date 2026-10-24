#!/usr/bin/env python3
"""Contribution nudges for brain: journal reading, nudge state, status rules and the answer CLI.

Stdlib only. Hooks call it with python3, never uv run. Later sections add the
SessionStart, SessionEnd and Stop commands to this same script.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

STALE_DAYS = 14
DRY_FEED_DAYS = 21
SESSION_PRUNE_DAYS = 7
BACKLOG_HOUR = 13
STOP_EDIT_THRESHOLD = 3
LOCK_WAIT = 1.0

NUDGE_SOURCES = ("startup", "resume", "clear")
GIT_TIMEOUT = 5
SCRIPT = Path(__file__).resolve()

DEFAULT_CONFIG = Path.home() / ".config" / "brain" / "config.json"
DEFAULT_JOURNALS = Path.home() / "private" / "projects"

# Any ## or ### heading that starts with a date is an entry, whatever follows.
ENTRY_HEADING = re.compile(r"^#{2,3}\s+(\d{4}-\d{2}-\d{2})", re.M)
# Only the date part counts, so a full ISO timestamp with offset is fine.
GRADUATED_AT = re.compile(r"<!--\s*brain-graduate:[^>]*?graduated_at=(\d{4}-\d{2}-\d{2})")


# --- config and state ---------------------------------------------------------

def config_path() -> Path:
    return Path(os.environ.get("BRAIN_CONFIG") or DEFAULT_CONFIG).expanduser()


def load_config() -> dict:
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def journals_dir(cfg: dict) -> Path:
    configured = cfg.get("journalsDir")
    return Path(configured).expanduser() if configured else DEFAULT_JOURNALS


def state_path() -> Path:
    # Resolved at call time so a test or a second config can repoint it.
    return config_path().parent / "nudge-state.json"


def load_state() -> dict:
    # A corrupt or missing file means empty state, never an error.
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state: dict) -> None:
    # Temp file in the same directory, then rename, so a reader never sees half a file.
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".nudge-state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lock_path() -> Path:
    return state_path().with_name("nudge-state.lock")


@contextlib.contextmanager
def state_lock():
    # Exclusive lock around one load-modify-save. Retries without blocking for about
    # LOCK_WAIT seconds, then proceeds unlocked so a hook never hangs a session.
    fd = None
    try:
        lock_path().parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
        deadline = _time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if _time.monotonic() >= deadline:
                    break
                _time.sleep(0.02)
    except OSError:
        pass
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)  # closing releases the flock


def nudges_off() -> bool:
    # Headless runs such as the weekly maintenance job set this, so they never touch nudge state.
    return os.environ.get("BRAIN_NUDGE_OFF") == "1"


# --- journals -----------------------------------------------------------------

def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def entry_dates(path: Path) -> list[date]:
    out = []
    for raw in ENTRY_HEADING.findall(_text(path)):
        try:
            out.append(date.fromisoformat(raw))
        except ValueError:
            continue
    return out


def newest_entry(path: Path) -> date:
    # mtime is only the fallback. A graduate run touches the marker and bumps
    # mtime without adding an entry, so it must not count when headings exist.
    dates = entry_dates(path)
    if dates:
        return max(dates)
    return date.fromtimestamp(path.stat().st_mtime)


def graduation_date(path: Path) -> date | None:
    m = GRADUATED_AT.search(_text(path))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def ungraduated(path: Path) -> bool:
    # Strictly later: an entry on the marker's day counts as graduated.
    graduated = graduation_date(path)
    return graduated is None or newest_entry(path) > graduated


def in_backlog(path: Path, first_run: datetime | None) -> bool:
    # A journal graduated on or after the first run was dug on purpose. It leaves the
    # backlog for good, and new entries in it are ordinary work. Older markers keep the
    # plain ungraduated rule.
    if not ungraduated(path):
        return False
    graduated = graduation_date(path)
    return not (graduated is not None and first_run is not None and graduated >= first_run.date())


def list_journals(d: Path) -> dict[str, Path]:
    try:
        return {p.stem: p for p in sorted(d.glob("*.md")) if p.is_file()}
    except OSError:
        return {}


# --- status rules and backlog cadence -----------------------------------------

def next_backlog_offer(now: datetime) -> datetime:
    # First Friday strictly after today, at BACKLOG_HOUR local. A no on a Friday
    # therefore waits a full week, whatever the hour.
    days = (4 - now.weekday()) % 7 or 7
    return datetime.combine(now.date() + timedelta(days=days), time(BACKLOG_HOUR))


def answer(state: dict, slug: str, verdict: str, today: date, journal: Path) -> dict:
    if verdict not in ("finished", "not-finished"):
        raise ValueError(f"unknown verdict: {verdict}")
    entry = state.setdefault("journals", {}).setdefault(slug, {})
    if verdict == "finished":
        entry["status"] = "finished"
    else:
        entry.update({
            "status": "snoozed",
            "last_asked": today.isoformat(),
            "asked_at_entry": newest_entry(journal).isoformat(),
        })
    return state


def answer_backlog_not_now(state: dict, now: datetime) -> dict:
    backlog = state.setdefault("backlog", {})
    backlog["next_offer_at"] = next_backlog_offer(now).isoformat(timespec="seconds")
    _clear_dig(backlog)
    return state


def _clear_dig(backlog: dict) -> None:
    backlog["dig_started_at"] = None
    backlog["dig_session"] = None
    backlog["interrupted"] = False


def backlog_start(state: dict, now: datetime) -> dict:
    backlog = state.setdefault("backlog", {})
    backlog["dig_started_at"] = now.isoformat(timespec="seconds")
    backlog["dig_session"] = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    backlog["interrupted"] = False
    return state


def backlog_slugs(state: dict, jdir: Path) -> list[str]:
    # Stored slugs that still exist and still hold ungraduated content. Ranked by
    # how much was written, then by how recently. Ranking by vault is the skill's job.
    journals = list_journals(jdir)
    ranked = []
    backlog = state.get("backlog")
    slugs = backlog.get("slugs") if isinstance(backlog, dict) else None
    first_run = _parse_dt(state.get("first_run_at"))
    for slug in slugs if isinstance(slugs, list) else []:
        path = journals.get(slug) if isinstance(slug, str) else None
        if path is None or not in_backlog(path, first_run):
            continue
        ranked.append((len(entry_dates(path)), newest_entry(path), slug))
    ranked.sort(key=lambda r: (-r[0], -r[1].toordinal()))
    return [slug for _, _, slug in ranked]


# --- SessionStart ---------------------------------------------------------------

def _d(value) -> dict:
    # State is user-editable JSON. Anything that is not the expected dict counts as empty.
    return value if isinstance(value, dict) else {}


def _sub(state: dict, key: str) -> dict:
    if not isinstance(state.get(key), dict):
        state[key] = {}
    return state[key]


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_dt(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _mark_asked(state: dict, slug: str, today: date) -> None:
    entry = _sub(state, "journals").get(slug)
    if not isinstance(entry, dict):
        entry = _sub(state, "journals")[slug] = {}
    entry["last_asked"] = today.isoformat()


def _command(*args: str) -> str:
    return " ".join(["python3", f'"{SCRIPT}"', *args])


def _noun(count: int) -> str:
    return "journal" if count == 1 else "journals"


def _tail(action: str) -> str:
    return (
        f"Offer this once, briefly, and only if it does not cut across what the user is already doing. "
        f"Run {action} ONLY on an explicit yes. "
        "If the user declines or does not answer, drop the subject and do not raise it again this session."
    )


def _ask_text(slug: str, lead: str) -> str:
    q = shlex.quote(slug)
    return (
        f"Journal check: {lead} Ask whether that project is finished.\n"
        f"Record the answer with one of these commands, run it after the user answers:\n"
        f"  {_command('answer', q, 'finished')}\n"
        f"  {_command('answer', q, 'not-finished')}\n"
        f"If it is finished, offer to run `/brain-graduate {slug}` right away.\n"
        + _tail(f"`/brain-graduate {slug}`")
    )


def _pending_text(slug: str) -> str:
    return _ask_text(slug, f"the last session worked on the progress journal `{slug}`.")


def _stale_text(slug: str, days: int) -> str:
    return _ask_text(slug, f"the progress journal `{slug}` has had no new entry for {days} days.")


def _graduate_text(slug: str) -> str:
    q = shlex.quote(slug)
    return (
        f"Journal check: the progress journal `{slug}` is finished but holds knowledge the brain does not have yet.\n"
        f"Offer to run `/brain-graduate {slug}`. If the user says it is not finished after all, record that with:\n"
        f"  {_command('answer', q, 'not-finished')}\n"
        + _tail(f"`/brain-graduate {slug}`")
    )


def _contribute_text(path: Path) -> str:
    return (
        f"Shared brain: the shared tree at {path} has changes that are not in a pull request yet. "
        "Offer to run `/brain-contribute`, which shows the pages and asks for confirmation before it pushes.\n"
        + _tail("`/brain-contribute`")
    )


def _backlog_text(count: int, resume: bool) -> str:
    not_now = _command("answer", "--backlog", "not-now")
    if resume:
        hold = "holds" if count == 1 else "hold"
        lead = (
            f"Backlog: an earlier dig was interrupted. {count} {_noun(count)} still {hold} knowledge the team does not have yet. "
            "Offer to continue with the rest."
        )
    else:
        verb = "was" if count == 1 else "were"
        lead = (
            f"Backlog: {count} {_noun(count)} {verb} never graduated. They hold knowledge the team does not have yet. "
            "Offer to graduate them all now. It runs as a batch and the user reviews the result once."
        )
    return (
        f"{lead}\n"
        "On a yes, run `/brain-graduate --backlog`. If the user declines or ignores the offer, record it with:\n"
        f"  {not_now}\n"
        + _tail("`/brain-graduate --backlog`")
    )


def _dry_feed_text(jdir: Path, days: int) -> str:
    return (
        f"Journal feed: {days} days after setup the journals directory {jdir} still holds no journal. "
        "The journaling feed rules may be missing from ~/.claude/CLAUDE.md. "
        "Offer to check whether they are there.\n"
        + _tail("the check")
    )


WEEKLY_WRAPPER = "brain-maintenance"


def _weekly_agent_text() -> str:
    return (
        "Weekly agent: the weekly maintenance agent on this machine predates the contribution nudges. "
        "Its Monday run would use up that day's nudges and end in a Stop block. "
        "Offer to re-run `/brain-init`, which refreshes the agent wrapper and plist.\n"
        + _tail("`/brain-init`")
    )


def _weekly_agent_nudge(state: dict, cfg: dict, today: date) -> str | None:
    # The wrapper sits next to the config file, where install_launchd writes it.
    # Any read problem means there is nothing to say.
    if _d(cfg.get("wiring")).get("launchdWeekly") is not True:
        return None
    if state.get("weekly_agent_notice") == today.isoformat():
        return None
    try:
        wrapper = (config_path().parent / WEEKLY_WRAPPER).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    if "BRAIN_NUDGE_OFF" in wrapper:
        return None
    state["weekly_agent_notice"] = today.isoformat()
    return _weekly_agent_text()


GENERATED = ("index.md", "log.md")


def _is_generated(path: str) -> bool:
    return path in GENERATED or path.rsplit("/", 1)[-1] == "MUSTKNOW.md"


def _git(path: Path, *args: str) -> str | None:
    # No fetch and no prompt, so a session start never waits on the network.
    try:
        r = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(path), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def shared_has_contribution(path: Path) -> bool:
    # Mirrors what brain-contribute would find, so the offer never ends in "nothing to contribute".
    if not path.is_dir():
        return False
    status = _git(path, "status", "--porcelain", "-z", "--untracked-files=no")
    if status is None:
        return False
    entries = status.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC" or entry[1] in "RC":
            i += 1  # a rename carries the old path as an extra entry
        if not _is_generated(entry[3:]):
            return True
    # New pages that git does not track yet. Ignored files stay out.
    others = _git(path, "ls-files", "--others", "--exclude-standard", "-z")
    for name in (others or "").split("\0"):
        if name.endswith(".md") and not _is_generated(name):
            return True
    if not _git(path, "for-each-ref", "--count=1", "--format=x", "refs/remotes/origin"):
        return False  # no remote to compare with, so no unpushed commits to speak of
    unpushed = _git(path, "rev-list", "--count", "HEAD", "--not", "--remotes=origin")
    return unpushed is not None and unpushed.strip().isdigit() and int(unpushed) > 0


def _record_session(state: dict, session_id, source, now: datetime) -> None:
    if isinstance(session_id, str) and session_id:
        sessions = _sub(state, "sessions")
        previous = _d(sessions.get(session_id))
        started = now.isoformat(timespec="seconds")
        # A compaction continues the same stretch of work, so it keeps the start time.
        if source == "compact" and isinstance(previous.get("started_at"), str):
            started = previous["started_at"]
        sessions[session_id] = {
            "started_at": started,
            "stop_fired_at": _d(sessions.get(session_id)).get("stop_fired_at"),
        }
    if "sessions" in state:
        sessions = _sub(state, "sessions")
        cutoff = now - timedelta(days=SESSION_PRUNE_DAYS)
        for sid in list(sessions):
            started = _parse_dt(_d(sessions[sid]).get("started_at"))
            if started is None or started < cutoff:
                del sessions[sid]


def _first_run(state: dict, journals: dict[str, Path], now: datetime) -> str:
    state["first_run_at"] = now.isoformat(timespec="seconds")
    slugs = [slug for slug, path in journals.items() if ungraduated(path)]
    # Silence counts as a no, so the next offer is already scheduled when this one goes out.
    state["backlog"] = {
        "slugs": slugs,
        "next_offer_at": next_backlog_offer(now).isoformat(timespec="seconds"),
        "dig_started_at": None,
    }
    return _backlog_text(len(slugs), resume=False) if slugs else ""


def _prune_backlog(state: dict, journals: dict[str, Path]) -> None:
    # A dug journal that later gets a new entry is ordinary work, not backlog.
    # A journal that is missing right now stays, so an unmounted directory loses nothing.
    backlog = state.get("backlog")
    if not isinstance(backlog, dict) or not isinstance(backlog.get("slugs"), list):
        return
    first_run = _parse_dt(state.get("first_run_at"))
    backlog["slugs"] = [
        s for s in backlog["slugs"]
        if isinstance(s, str) and (s not in journals or in_backlog(journals[s], first_run))
    ]


def _backlog_nudge(state: dict, remaining: list[str], now: datetime) -> str | None:
    backlog = _d(state.get("backlog"))
    if not remaining:
        if backlog.get("dig_started_at") or backlog.get("dig_session") or backlog.get("interrupted"):
            _clear_dig(backlog)
        return None
    if backlog.get("dig_session"):
        resume = backlog.get("interrupted") is True  # a dig that is still running is not interrupted
    else:
        resume = bool(backlog.get("dig_started_at"))  # no session id was recorded, so fall back
    due = _parse_dt(backlog.get("next_offer_at"))
    if not resume and due is not None and due > now:
        return None
    # Silence counts as a no. A yes restarts the dig through `backlog start`.
    backlog["next_offer_at"] = next_backlog_offer(now).isoformat(timespec="seconds")
    _clear_dig(backlog)
    return _backlog_text(len(remaining), resume=resume)


def _stale_nudge(state: dict, journals: dict[str, Path], skip: set[str], today: date) -> str | None:
    jstate = _d(state.get("journals"))
    candidates = []
    for slug, path in journals.items():
        if slug in skip or not ungraduated(path):
            continue
        status = _d(jstate.get(slug)).get("status")
        if status not in (None, "open", "snoozed"):
            continue
        newest = newest_entry(path)
        if (today - newest).days < STALE_DAYS:
            continue
        asked = _parse_date(_d(jstate.get(slug)).get("last_asked"))
        if asked is not None and (today - asked).days < STALE_DAYS:
            continue
        candidates.append((newest, slug))
    if not candidates:
        return None
    newest, slug = min(candidates)
    _mark_asked(state, slug, today)
    return _stale_text(slug, (today - newest).days)


def _pick(state: dict, cfg: dict, jdir: Path, journals: dict[str, Path], now: datetime) -> str:
    today = now.date()
    jstate = _d(state.get("journals"))
    remaining = backlog_slugs(state, jdir)
    skip = set(remaining)

    # 1. Pending ask.
    pending = state.get("pending_ask")
    for slug in pending if isinstance(pending, list) else []:
        path = journals.get(slug) if isinstance(slug, str) else None
        if path is None or slug in skip or not ungraduated(path):
            continue
        if _d(jstate.get(slug)).get("last_asked") == today.isoformat():
            continue  # at most once a day per journal
        if _d(jstate.get(slug)).get("status") in (None, "open"):
            _mark_asked(state, slug, today)
            return _pending_text(slug)

    # 2. Finished but not graduated.
    for slug in sorted(jstate):
        path = journals.get(slug)
        if path is None or slug in skip or _d(jstate[slug]).get("status") != "finished":
            continue
        if _d(jstate[slug]).get("last_asked") == today.isoformat():
            continue  # at most once a day per journal
        if ungraduated(path):
            _mark_asked(state, slug, today)
            return _graduate_text(slug)

    # Priorities 3, 4 and 6 run once a day. 5 runs every start, so a Friday
    # morning start does not use up the 13:00 slot.
    daily = state.get("daily_stamp") != today.isoformat()
    if daily:
        state["daily_stamp"] = today.isoformat()
        text = _stale_nudge(state, journals, skip, today)
        if text:
            return text
        shared = _d(cfg.get("sharedTree")).get("path")
        if isinstance(shared, str) and shared and shared_has_contribution(Path(shared).expanduser()):
            return _contribute_text(Path(shared).expanduser())

    text = _backlog_nudge(state, remaining, now)
    if text:
        return text

    if daily and not journals:
        first = _parse_dt(state.get("first_run_at"))
        shown = _parse_date(state.get("dry_feed_shown"))
        if first is not None and (today - first.date()).days >= DRY_FEED_DAYS:
            if shown is None or (today - shown).days >= DRY_FEED_DAYS:
                state["dry_feed_shown"] = today.isoformat()
                return _dry_feed_text(jdir, (today - first.date()).days)
    return ""


def cmd_start(hook: dict, now: datetime) -> str:
    with state_lock():
        return _start(_d(hook), now)


def _start(hook: dict, now: datetime) -> str:
    state = load_state()
    before = json.dumps(state, sort_keys=True)
    _record_session(state, hook.get("session_id"), hook.get("source"), now)
    text = ""
    if hook.get("source") in NUDGE_SOURCES and not nudges_off():
        cfg = load_config()
        jdir = journals_dir(cfg)
        journals = list_journals(jdir)
        if not state.get("first_run_at"):
            text = _first_run(state, journals, now)
            state["pending_ask"] = []
            if not text:
                text = _weekly_agent_nudge(state, cfg, now.date()) or ""
        else:
            # A finished journal that graduated is open again, whatever the user said before.
            for slug, entry in _d(state.get("journals")).items():
                path = journals.get(slug)
                if isinstance(entry, dict) and entry.get("status") == "finished" and path and not ungraduated(path):
                    entry["status"] = "open"
            _prune_backlog(state, journals)
            # Right after the first-run offer and ahead of everything else. A displaced
            # nudge and a pending ask wait for the next start, because _pick did not run.
            text = _weekly_agent_nudge(state, cfg, now.date()) or ""
            if not text:
                text = _pick(state, cfg, jdir, journals, now)
                if state.get("pending_ask"):
                    state["pending_ask"] = []
    if json.dumps(state, sort_keys=True) != before:
        save_state(state)
    return text


# --- SessionEnd -----------------------------------------------------------------

def cmd_end(hook: dict) -> None:
    # Budget is 1.5 s. Read the state, stat the journals, write one small file. No git, no transcript.
    session_id = _d(hook).get("session_id")
    if nudges_off() or not isinstance(session_id, str) or not session_id:
        return
    with state_lock():
        _end(session_id)


def _end(session_id: str) -> None:
    state = load_state()
    changed = False
    backlog = _d(state.get("backlog"))
    if backlog.get("dig_session") == session_id:
        # The dig's own session ended. A leftover backlog makes the next start offer a resume.
        if backlog_slugs(state, journals_dir(load_config())):
            backlog["interrupted"] = True
        else:
            _clear_dig(backlog)
        changed = True
    started = _parse_dt(_d(_d(state.get("sessions")).get(session_id)).get("started_at"))
    if started is None:
        if changed:
            save_state(state)
        return
    pending = state.get("pending_ask")
    pending = list(pending) if isinstance(pending, list) else []
    added = False
    for slug, path in list_journals(journals_dir(load_config())).items():
        try:
            touched = datetime.fromtimestamp(path.stat().st_mtime) > started
        except OSError:
            continue
        if touched and slug not in pending:
            pending.append(slug)
            added = True
    if added:
        state["pending_ask"] = pending
    if added or changed:
        save_state(state)


# --- Stop -----------------------------------------------------------------------

FILE_TOOLS = ("Edit", "Write", "NotebookEdit")
# Tracker CLIs whose writes count as project work: GitHub and Gitea.
REMOTE_CLIS = ("gh", "tea")
# One write-verb set for every tracker CLI. Whole tokens only.
REMOTE_WRITE_VERBS = frozenset((
    "create", "update", "add", "merge", "approve", "transition", "delete", "move",
    "remove", "unapprove", "decline", "reopen", "upload", "comment", "edit", "assign", "label",
))
# A read word anywhere in the name keeps a read a read: get_transitions, can_merge, get_pr_comment.
REMOTE_READ_WORDS = frozenset(("get", "list", "can", "search", "find", "view", "show", "read", "download", "diff"))
GIT_WRITE = re.compile(r"\bgit\s+(?:-[cC]\s+\S+\s+)*(?:commit|push)\b")
WORDS = re.compile(r"[a-z]+")


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _journal_spellings(jdir: Path, raw: str | None) -> set[str]:
    # Every way a shell command can name the journals directory: the expanded and the
    # real path, the literal configured string, and the ~/, $HOME/ and ${HOME}/ forms.
    bases = {str(jdir), _real(str(jdir))}
    if raw:
        bases.add(raw)
        bases.add(os.path.expanduser(raw))
    bases = {b.rstrip("/") for b in bases if b.rstrip("/")}
    out = set(bases)
    home = os.path.expanduser("~")
    for h in {home, os.path.realpath(home)}:
        for b in bases:
            if b != h and _under(b, h):
                rel = b[len(h.rstrip("/")) + 1:]
                out.update((f"~/{rel}", f"$HOME/{rel}", f"${{HOME}}/{rel}"))
    return out


def _journal_write_re(spellings: set[str]) -> re.Pattern:
    # The write operator must sit directly before the path, so `grep x <dir>/a.md 2>/dev/null` is a read.
    alt = "|".join(re.escape(sp) for sp in sorted(spellings, key=len, reverse=True))
    path = rf"[\"']?(?:{alt})/"
    return re.compile(
        rf">>?\s*{path}"
        rf"|\btee\s+(?:-\S+\s+)*{path}"
        rf"|\bsed\b[^|&\n]*\s-(?:[A-Za-z]*i|-in-place)\S*[^|&\n]*?{path}"
    )


def _is_remote_write(tokens: list[str]) -> bool:
    words = [w for token in tokens for w in WORDS.findall(token.lower())]
    return bool(REMOTE_WRITE_VERBS.intersection(words)) and not REMOTE_READ_WORDS.intersection(words)


def _vault_roots(cfg: dict) -> list[str]:
    roots = []
    for key in ("personalVault", "sharedTree", "privateVault"):
        raw = _d(cfg.get(key)).get("path")
        if isinstance(raw, str) and raw:
            roots.append(raw)
    return roots


def _root_spellings(roots) -> set[str]:
    out = set()
    for raw in roots or ():
        for form in (os.path.expanduser(raw), _real(raw)):
            if form.rstrip("/"):
                out.add(form.rstrip("/"))
    return out


def _classify(name: str, inp: dict, jdir: str, journal_write: re.Pattern, vaults: set[str] = frozenset()) -> str | None:
    if name in FILE_TOOLS:
        target = inp.get("file_path") or inp.get("notebook_path")
        if isinstance(target, str) and target:
            if _under(_real(target), jdir):
                return "journal"
            # The brain skills write the vault themselves. That is not project work.
            expanded = os.path.expanduser(target)
            if any(_under(expanded, v) or _under(_real(target), v) for v in vaults):
                return None
        return "edit"
    if name == "Bash":
        cmd = inp.get("command")
        if not isinstance(cmd, str):
            return None
        if journal_write.search(cmd):
            return "journal"
        if GIT_WRITE.search(cmd):
            return "commit"
        words = cmd.split()
        if words[:1] and words[0] in REMOTE_CLIS and _is_remote_write(words[1:4]):
            return "remote_write"
        return None
    return None


def scan_transcript(path: Path, jdir: Path, raw_jdir: str | None = None,
                    vault_roots: list[str] | None = None) -> tuple[list[tuple[int, str]], int]:
    # Tool uses are numbered across the whole transcript, 0-based. A line that is not
    # JSON (a partly written last line) or not an assistant message is skipped.
    events: list[tuple[int, str]] = []
    index = -1
    real = _real(str(jdir))
    journal_write = _journal_write_re(_journal_spellings(jdir, raw_jdir))
    vaults = _root_spellings(vault_roots)
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return events, index
    with f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            message = _d(obj).get("message")
            if _d(obj).get("type") != "assistant" or not isinstance(_d(message).get("content"), list):
                continue
            for item in message["content"]:
                item = _d(item)
                if item.get("type") != "tool_use" or not isinstance(item.get("name"), str):
                    continue
                index += 1
                kind = _classify(item["name"], _d(item.get("input")), real, journal_write, vaults)
                if kind:
                    events.append((index, kind))
    return events, index


def _in_linked_worktree(cwd: str) -> bool:
    # A linked worktree has its own git dir under the main one. Any git failure means not a worktree.
    git_dir = _git(Path(cwd), "rev-parse", "--absolute-git-dir")
    common = _git(Path(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not git_dir or not common:
        return False
    return _real(git_dir.strip()) != _real(common.strip())


def _stop_reason(jdir: Path) -> str:
    return (
        "Before you hand back: this session did substantive work since the journal was last updated. "
        f"If a journal under {jdir} covers this work, append a dated entry with the results. "
        "If none exists and the work is substantive, create one per the progress journaling rule. "
        "If the work was trivial, say so in one line and stop. "
        "Do not mention this check otherwise."
    )


def cmd_stop(hook: dict, now: datetime | None = None) -> dict | None:
    hook = _d(hook)
    session_id, transcript = hook.get("session_id"), hook.get("transcript_path")
    if nudges_off() or hook.get("stop_hook_active") or not isinstance(session_id, str) or not session_id:
        return None
    cfg = load_config()
    if not cfg.get("journalsDir") or not isinstance(transcript, str) or not transcript:
        return None
    jdir = journals_dir(cfg)
    events, last = scan_transcript(Path(transcript), jdir, str(cfg["journalsDir"]), _vault_roots(cfg))
    with state_lock():
        return _stop(hook, session_id, events, last, jdir, now)


def _stop(hook: dict, session_id: str, events: list, last: int, jdir: Path, now: datetime | None) -> dict | None:
    state = load_state()
    sessions = _sub(state, "sessions")
    entry = _d(sessions.get(session_id))
    fired = entry.get("stop_fired_at")
    last_journal = max((i for i, kind in events if kind == "journal"), default=-1)
    if isinstance(fired, int) and not isinstance(fired, bool) and last_journal <= fired:
        return None  # already fired in this stretch, and no journal write since
    since = [kind for i, kind in events if i > last_journal]
    if since.count("commit") < 1 and sum(k in ("edit", "remote_write") for k in since) < STOP_EDIT_THRESHOLD:
        return None
    cwd = hook.get("cwd")
    if _in_linked_worktree(cwd if isinstance(cwd, str) and cwd else os.getcwd()):
        return None
    now = now or datetime.now()
    # Mark the stretch before emitting, so the follow-up reply passes through.
    # A session entry always carries a valid started_at, or the next start prunes it.
    if _parse_dt(entry.get("started_at")) is None:
        entry["started_at"] = now.isoformat(timespec="seconds")
    entry["stop_fired_at"] = last
    sessions[session_id] = entry
    save_state(state)
    return {"decision": "block", "reason": _stop_reason(jdir)}


# --- CLI ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brain_nudge.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_answer = sub.add_parser("answer", help="record the user's answer about a journal or the backlog")
    p_answer.add_argument("slug", nargs="?", help="journal slug")
    p_answer.add_argument("verdict", nargs="?", choices=["finished", "not-finished"])
    p_answer.add_argument("--backlog", choices=["not-now"], help="decline the Friday backlog offer")

    sub.add_parser("start", help="SessionStart hook: read hook JSON on stdin, print the payload")
    sub.add_parser("stop", help="Stop hook: print a block payload when the journal needs an update, else {}")
    sub.add_parser("end", help="SessionEnd hook: record the journals the session touched, print nothing")

    p_backlog = sub.add_parser("backlog", help="list or start the backlog dig")
    p_backlog.add_argument("action", choices=["list", "start"])
    return parser


def cmd_answer(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.backlog:
        if args.slug or args.verdict:
            parser.error("answer --backlog takes no slug or verdict")
        with state_lock():
            save_state(answer_backlog_not_now(load_state(), datetime.now()))
        return 0
    if not args.slug or not args.verdict:
        parser.error("answer needs <slug> finished|not-finished, or --backlog not-now")
    # Look the slug up in the journal listing, so a slug cannot point outside journalsDir.
    journal = list_journals(journals_dir(load_config())).get(args.slug)
    if journal is None:
        print(f"brain_nudge: no journal named {args.slug}", file=sys.stderr)
        return 2
    with state_lock():
        save_state(answer(load_state(), args.slug, args.verdict, date.today(), journal))
    return 0


def cmd_backlog(args: argparse.Namespace) -> int:
    if args.action == "list":
        for slug in backlog_slugs(load_state(), journals_dir(load_config())):
            print(slug)
        return 0
    with state_lock():
        save_state(backlog_start(load_state(), datetime.now()))
    return 0


def _read_hook() -> dict:
    raw = sys.stdin.read()
    try:
        hook = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return hook if isinstance(hook, dict) else {}


def cmd_start_cli() -> int:
    # The hook must never crash a session. Any failure prints the empty payload.
    text = ""
    try:
        text = cmd_start(_read_hook(), datetime.now())
    except Exception:
        text = ""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


def cmd_end_cli() -> int:
    # SessionEnd output is discarded and a crash must not show. Print nothing, exit 0.
    try:
        cmd_end(_read_hook())
    except Exception:
        pass
    return 0


def cmd_stop_cli() -> int:
    # The hook must never crash a session. Anything unexpected prints the empty payload.
    payload: dict = {}
    try:
        payload = cmd_stop(_read_hook()) or {}
    except Exception:
        payload = {}
    print(json.dumps(payload))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "answer":
        return cmd_answer(args, parser)
    if args.command == "start":
        return cmd_start_cli()
    if args.command == "end":
        return cmd_end_cli()
    if args.command == "stop":
        return cmd_stop_cli()
    return cmd_backlog(args)


if __name__ == "__main__":
    sys.exit(main())
