import importlib.util
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent.parent
SCRIPT = HERE / "scripts" / "brain_nudge.py"
spec = importlib.util.spec_from_file_location("brain_nudge", SCRIPT)
bn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bn)


@pytest.fixture
def env(tmp_path, monkeypatch):
    journals = tmp_path / "journals"
    shared = tmp_path / "shared"
    personal = tmp_path / "personal"
    for d in (journals, shared, personal):
        d.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "journalsDir": str(journals),
        "sharedTree": {"path": str(shared)},
        "personalVault": {"path": str(personal)},
    }))
    monkeypatch.setenv("BRAIN_CONFIG", str(cfg))
    return SimpleNamespace(root=tmp_path, journals=journals, shared=shared, cfg=cfg)


def run_cli(env, *args, stdin=""):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin, text=True, capture_output=True,
        env={**os.environ, "BRAIN_CONFIG": str(env.cfg)},
    )


# Task 1: state and journal reading

def test_newest_entry_reads_heading_variants(tmp_path):
    j = tmp_path / "a.md"
    j.write_text("# A\n## Primer / tldr;\n## 2026-10-01\nx\n## 2026-10-10 — Friday\n### 2026-10-12 (later)\n")
    assert bn.newest_entry(j) == date(2026, 10, 12)


def test_newest_entry_falls_back_to_mtime(tmp_path):
    j = tmp_path / "b.md"
    j.write_text("# B\nno dates\n")
    os.utime(j, (datetime(2026, 9, 3).timestamp(),) * 2)
    assert bn.newest_entry(j) == date(2026, 9, 3)


def test_marker_same_day_counts_as_graduated(tmp_path):
    j = tmp_path / "c.md"
    j.write_text("<!-- brain-graduate: graduated_at=2026-10-10T15:00:00+02:00 graduated_to=[[x]] -->\n# C\n## 2026-10-10\n")
    assert bn.graduation_date(j) == date(2026, 10, 10)
    assert bn.ungraduated(j) is False
    j.write_text(j.read_text() + "## 2026-10-11\n")
    assert bn.ungraduated(j) is True


def test_no_marker_is_ungraduated(tmp_path):
    j = tmp_path / "d.md"
    j.write_text("# D\n## 2026-10-01\n")
    assert bn.graduation_date(j) is None
    assert bn.ungraduated(j) is True


def test_list_journals_skips_subdirs(tmp_path):
    (tmp_path / "x.md").write_text("# X\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.md").write_text("# Y\n")
    assert set(bn.list_journals(tmp_path)) == {"x"}


def test_state_roundtrip_and_corrupt(env):
    bn.save_state({"a": 1})
    assert bn.load_state() == {"a": 1}
    bn.state_path().write_text("{not json")
    assert bn.load_state() == {}


def test_state_path_follows_config_and_missing_is_empty(env):
    assert bn.state_path() == env.root / "nudge-state.json"
    assert bn.load_state() == {}


def test_journals_dir_default_and_config(env, monkeypatch):
    assert bn.journals_dir(bn.load_config()) == env.journals
    assert bn.journals_dir({}) == Path.home() / "private" / "projects"


def test_load_config_error_is_empty(env):
    env.cfg.write_text("{broken")
    assert bn.load_config() == {}


# Task 2: status rules, Friday cadence, answer CLI

@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 10, 19, 9), datetime(2026, 10, 23, 13)),   # Monday
    (datetime(2026, 10, 23, 9), datetime(2026, 10, 30, 13)),   # Friday morning
    (datetime(2026, 10, 23, 15), datetime(2026, 10, 30, 13)),  # Friday afternoon
    (datetime(2026, 10, 24, 9), datetime(2026, 10, 30, 13)),   # Saturday
])
def test_next_backlog_offer(now, expected):
    assert bn.next_backlog_offer(now) == expected


def test_not_finished_snoozes(tmp_path):
    j = tmp_path / "p.md"
    j.write_text("# P\n## 2026-10-20\n")
    s = bn.answer({}, "p", "not-finished", date(2026, 10, 23), j)
    assert s["journals"]["p"] == {"status": "snoozed", "last_asked": "2026-10-23", "asked_at_entry": "2026-10-20"}


def test_finished_sets_status(tmp_path):
    j = tmp_path / "p.md"
    j.write_text("# P\n## 2026-10-20\n")
    assert bn.answer({}, "p", "finished", date(2026, 10, 23), j)["journals"]["p"]["status"] == "finished"


def test_backlog_list_drops_graduated(env):
    jd = env.journals
    (jd / "old.md").write_text("# O\n## 2026-01-01\n## 2026-01-02\n")
    (jd / "done.md").write_text("<!-- brain-graduate: graduated_at=2026-02-01T00:00:00Z graduated_to=[[x]] -->\n## 2026-01-01\n")
    assert bn.backlog_slugs({"backlog": {"slugs": ["old", "done"]}}, jd) == ["old"]


def test_backlog_ranks_by_entry_count_then_newest(env):
    jd = env.journals
    (jd / "few.md").write_text("# F\n## 2026-03-01\n")
    (jd / "many.md").write_text("# M\n## 2026-01-01\n## 2026-01-02\n## 2026-01-03\n")
    (jd / "fresh.md").write_text("# R\n## 2026-05-01\n")
    (jd / "gone.md").write_text("# G\n## 2026-01-01\n")
    state = {"backlog": {"slugs": ["few", "many", "fresh", "gone", "missing"]}}
    (jd / "gone.md").unlink()
    assert bn.backlog_slugs(state, jd) == ["many", "fresh", "few"]


def test_backlog_start_sets_timestamp(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    now = datetime(2026, 10, 23, 14, 5, 6)
    s = bn.backlog_start({"backlog": {"slugs": ["a"]}}, now)
    assert s["backlog"] == {"slugs": ["a"], "dig_started_at": "2026-10-23T14:05:06",
                            "dig_session": None, "interrupted": False}


def test_backlog_not_now_sets_next_offer():
    s = bn.answer_backlog_not_now({}, datetime(2026, 10, 23, 15))
    assert s["backlog"]["next_offer_at"] == "2026-10-30T13:00:00"


def test_cli_answer_backlog_not_now_writes_state(env):
    r = run_cli(env, "answer", "--backlog", "not-now")
    assert r.returncode == 0
    assert "next_offer_at" in bn.load_state()["backlog"]


def test_cli_answer_slug_writes_state(env):
    (env.journals / "p.md").write_text("# P\n## 2026-10-20\n")
    r = run_cli(env, "answer", "p", "not-finished")
    assert r.returncode == 0
    entry = bn.load_state()["journals"]["p"]
    assert entry["status"] == "snoozed"
    assert entry["asked_at_entry"] == "2026-10-20"


def test_cli_answer_unknown_slug_exits_2(env):
    r = run_cli(env, "answer", "nope", "finished")
    assert r.returncode == 2
    assert "nope" in r.stderr
    assert not bn.state_path().exists()


def test_cli_backlog_list_and_start(env):
    (env.journals / "a.md").write_text("# A\n## 2026-01-01\n")
    (env.journals / "b.md").write_text("# B\n## 2026-01-01\n## 2026-01-02\n")
    bn.save_state({"backlog": {"slugs": ["a", "b"]}})
    r = run_cli(env, "backlog", "list")
    assert r.stdout.splitlines() == ["b", "a"]
    assert run_cli(env, "backlog", "start").returncode == 0
    state = bn.load_state()
    datetime.fromisoformat(state["backlog"]["dig_started_at"])
    assert state["backlog"]["slugs"] == ["a", "b"]


# Task 3: SessionStart command

NOW = datetime(2026, 10, 23, 13, 30)  # a Friday
TODAY = NOW.date()
START = {"session_id": "s1", "source": "startup"}


def jr(env, slug, *dates, marker=None):
    body = f"<!-- brain-graduate: graduated_at={marker}T10:00:00Z graduated_to=[[x]] -->\n" if marker else ""
    body += f"# {slug}\n" + "".join(f"## {d}\nwork\n" for d in dates)
    (env.journals / f"{slug}.md").write_text(body)
    return env.journals / f"{slug}.md"


def seed_state(**kw):
    # A started install: first run happened recently, so no dry feed and no backlog yet.
    state = {"first_run_at": "2026-10-20T09:00:00"}
    state.update(kw)
    bn.save_state(state)


def ago(days):
    return (TODAY - timedelta(days=days)).isoformat()


def sh(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def make_shared(env):
    origin, seed = env.root / "origin.git", env.root / "seed"
    subprocess.run(["git", "init", "--quiet", "--bare", str(origin)], check=True)
    sh(origin, "symbolic-ref", "HEAD", "refs/heads/master")
    subprocess.run(["git", "clone", "--quiet", str(origin), str(seed)], check=True, capture_output=True)
    for repo in (seed,):
        sh(repo, "config", "user.email", "test@example.com")
        sh(repo, "config", "user.name", "Test")
    sh(seed, "checkout", "--quiet", "-b", "master")
    (seed / "wiki").mkdir()
    for name in ("page.md", "index.md", "log.md", "wiki/MUSTKNOW.md", "wiki/page.md"):
        (seed / name).write_text("one\n")
    sh(seed, "add", "-A")
    sh(seed, "commit", "--quiet", "-m", "seed")
    sh(seed, "push", "--quiet", "-u", "origin", "master")
    subprocess.run(["git", "clone", "--quiet", str(origin), str(env.shared)], check=True, capture_output=True)
    sh(env.shared, "config", "user.email", "test@example.com")
    sh(env.shared, "config", "user.name", "Test")
    return env.shared


def test_first_run_fills_backlog_and_offers(env):
    jr(env, "a", "2026-01-01")
    out = bn.cmd_start(START, NOW)
    assert "1 journal " in out
    assert "/brain-graduate --backlog" in out
    assert "explicit yes" in out
    state = bn.load_state()
    assert state["backlog"]["slugs"] == ["a"]
    assert state["first_run_at"] == "2026-10-23T13:30:00"


def test_first_run_ignores_graduated_and_is_silent_when_empty(env):
    jr(env, "done", "2026-01-01", marker="2026-02-01")
    assert bn.cmd_start(START, NOW) == ""
    state = bn.load_state()
    assert state["first_run_at"]
    assert state["backlog"]["slugs"] == []


def test_first_run_offer_beats_other_nudges(env):
    jr(env, "a", "2026-01-01", "2026-01-02")
    jr(env, "b", "2026-01-01")
    out = bn.cmd_start(START, NOW)
    assert "2 journals" in out


def test_compact_never_nudges(env):
    jr(env, "a", "2026-01-01")
    assert bn.cmd_start({"session_id": "s1", "source": "compact"}, NOW) == ""
    state = bn.load_state()
    assert "first_run_at" not in state
    assert "s1" in state["sessions"]


def test_missing_source_and_session_id_stay_silent(env):
    jr(env, "a", "2026-01-01")
    assert bn.cmd_start({}, NOW) == ""
    assert bn.load_state().get("sessions", {}) == {}
    assert "first_run_at" not in bn.load_state()


def test_session_recorded_and_old_sessions_pruned(env):
    old = (NOW - timedelta(days=8)).isoformat(timespec="seconds")
    recent = (NOW - timedelta(days=6)).isoformat(timespec="seconds")
    seed_state(sessions={
        "old": {"started_at": old, "stop_fired_at": None},
        "recent": {"started_at": recent, "stop_fired_at": None},
        "junk": "x",
        "s1": {"started_at": old, "stop_fired_at": 5},
    })
    bn.cmd_start(START, NOW)
    sessions = bn.load_state()["sessions"]
    assert set(sessions) == {"recent", "s1"}
    assert sessions["s1"] == {"started_at": "2026-10-23T13:30:00", "stop_fired_at": 5}


def test_new_session_has_no_stop_fired(env):
    seed_state()
    bn.cmd_start(START, NOW)
    assert bn.load_state()["sessions"]["s1"] == {"started_at": "2026-10-23T13:30:00", "stop_fired_at": None}


def test_backlog_reoffer_waits_for_friday(env):
    jr(env, "a", "2026-01-01")
    monday = datetime(2026, 10, 19, 10)
    state = bn.answer_backlog_not_now({"first_run_at": "2026-10-19T09:00:00", "backlog": {"slugs": ["a"]}}, monday)
    bn.save_state(state)
    assert bn.cmd_start(START, datetime(2026, 10, 22, 15)) == ""
    assert bn.cmd_start(START, datetime(2026, 10, 23, 9)) == ""
    assert "/brain-graduate --backlog" in bn.cmd_start(START, datetime(2026, 10, 23, 13, 5))


def test_missed_friday_is_caught_up_on_monday(env):
    jr(env, "a", "2026-01-01")
    bn.save_state({"first_run_at": "2026-10-12T09:00:00",
                   "backlog": {"slugs": ["a"], "next_offer_at": "2026-10-23T13:00:00"}})
    assert "/brain-graduate --backlog" in bn.cmd_start(START, datetime(2026, 10, 26, 8))


def test_morning_start_does_not_burn_friday_slot(env):
    jr(env, "a", "2026-01-01")
    bn.save_state({"first_run_at": "2026-10-12T09:00:00",
                   "backlog": {"slugs": ["a"], "next_offer_at": "2026-10-23T13:00:00"}})
    assert bn.cmd_start(START, datetime(2026, 10, 23, 9)) == ""
    assert "/brain-graduate --backlog" in bn.cmd_start(START, datetime(2026, 10, 23, 13, 30))


def test_backlog_offer_names_not_now_command(env):
    jr(env, "a", "2026-01-01")
    seed_state(backlog={"slugs": ["a"]})
    out = bn.cmd_start(START, NOW)
    assert f'python3 "{Path(bn.__file__).resolve()}" answer --backlog not-now' in out


def test_interrupted_dig_resumes_immediately(env):
    jr(env, "left", "2026-01-01")
    jr(env, "dug", "2026-01-01", marker="2026-10-22")
    seed_state(backlog={"slugs": ["left", "dug"], "next_offer_at": "2026-12-01T13:00:00",
                        "dig_started_at": "2026-10-22T10:00:00"})
    out = bn.cmd_start(START, NOW)
    assert "/brain-graduate --backlog" in out
    assert "interrupted" in out
    assert "1 journal " in out


def test_finished_dig_clears_dig_started_and_stays_silent(env):
    jr(env, "dug", "2026-01-01", marker="2026-10-22")
    seed_state(backlog={"slugs": ["dug"], "dig_started_at": "2026-10-22T10:00:00"})
    assert bn.cmd_start(START, NOW) == ""
    assert bn.load_state()["backlog"]["dig_started_at"] is None


def test_dug_journal_with_new_entry_is_not_back_in_backlog(env):
    jr(env, "dug", "2026-01-01", marker="2026-10-01")
    seed_state(backlog={"slugs": ["dug"]})
    assert bn.cmd_start(START, NOW) == ""
    assert bn.load_state()["backlog"]["slugs"] == []
    jr(env, "dug", "2026-01-01", "2026-10-22", marker="2026-10-01")
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW) == ""
    assert bn.load_state()["backlog"]["slugs"] == []


def test_dug_journal_with_later_entry_does_not_interrupt_dig_and_shows_pending_ask(env):
    # D1: the dig marks the journal after first_run_at, the user then adds an entry.
    jr(env, "dug", "2026-10-10", "2026-10-24", marker="2026-10-23")
    jr(env, "left", "2026-01-01")
    seed_state(backlog={"slugs": ["dug"], "dig_started_at": "2026-10-23T10:00:00", "dig_session": "dig1"},
               pending_ask=["dug"])
    assert bn.backlog_slugs(bn.load_state(), env.journals) == []
    bn.cmd_end({"session_id": "dig1"})
    backlog = bn.load_state()["backlog"]
    assert backlog["interrupted"] is False
    assert backlog["dig_started_at"] is None
    out = bn.cmd_start({"session_id": "s9", "source": "startup"}, datetime(2026, 10, 26, 9))
    assert "`dug`" in out
    assert "Backlog" not in out
    assert bn.load_state()["backlog"]["slugs"] == []


def test_prune_removes_journal_dug_after_first_run_even_with_new_entry(env):
    jr(env, "dug", "2026-10-10", "2026-10-24", marker="2026-10-20")  # same day as first_run_at
    seed_state(backlog={"slugs": ["dug"], "next_offer_at": "2099-01-01T13:00:00"})
    bn.cmd_start(START, NOW)
    assert bn.load_state()["backlog"]["slugs"] == []


def test_pre_first_run_marker_with_newer_entries_stays_in_backlog(env):
    jr(env, "old", "2026-10-01", "2026-10-22", marker="2026-10-05")  # marked before first_run_at 2026-10-20
    seed_state(backlog={"slugs": ["old"], "next_offer_at": "2099-01-01T13:00:00"})
    assert bn.backlog_slugs(bn.load_state(), env.journals) == ["old"]
    bn.cmd_start(START, NOW)
    assert bn.load_state()["backlog"]["slugs"] == ["old"]


def test_backlog_slugs_without_first_run_keeps_old_behaviour(env):
    jr(env, "a", "2026-10-01", "2026-10-22", marker="2026-10-05")
    assert bn.backlog_slugs({"backlog": {"slugs": ["a"]}}, env.journals) == ["a"]


@pytest.mark.parametrize("count,verb,noun", [(1, "holds", "journal"), (2, "hold", "journals")])
def test_resume_text_grammar(count, verb, noun):
    text = bn._backlog_text(count, resume=True)
    assert f"{count} {noun} still {verb} knowledge" in text


def test_pending_ask_for_open_journal(env):
    jr(env, "p", "2026-10-22")
    seed_state(pending_ask=["p"])
    out = bn.cmd_start(START, NOW)
    assert "`p`" in out
    assert "/brain-graduate p" in out
    assert "explicit yes" in out
    py = f'python3 "{Path(bn.__file__).resolve()}"'
    assert f"{py} answer p not-finished" in out
    assert f"{py} answer p finished" in out
    assert bn.load_state()["pending_ask"] == []


def test_pending_ask_skips_snoozed_graduated_and_backlog(env):
    jr(env, "snoozed", "2026-10-22")
    jr(env, "graduated", "2026-10-20", marker="2026-10-21")
    jr(env, "inback", "2026-10-22")
    jr(env, "missing_ok", "2026-10-22")
    (env.journals / "missing_ok.md").unlink()
    seed_state(pending_ask=["snoozed", "graduated", "inback", "missing_ok"],
               journals={"snoozed": {"status": "snoozed", "last_asked": ago(1), "asked_at_entry": ago(1)}},
               backlog={"slugs": ["inback"], "next_offer_at": "2099-01-01T13:00:00"})
    assert bn.cmd_start(START, NOW) == ""
    assert bn.load_state()["pending_ask"] == []


def test_pending_ask_not_cleared_on_compact(env):
    jr(env, "p", "2026-10-22")
    seed_state(pending_ask=["p"])
    assert bn.cmd_start({"session_id": "s1", "source": "compact"}, NOW) == ""
    assert bn.load_state()["pending_ask"] == ["p"]


def test_finished_ungraduated_offers_graduate(env):
    jr(env, "f", "2026-10-22")
    seed_state(journals={"f": {"status": "finished"}})
    out = bn.cmd_start(START, NOW)
    assert "/brain-graduate f" in out
    assert "explicit yes" in out


def test_finished_graduated_returns_to_open(env):
    jr(env, "f", "2026-10-22", marker="2026-10-23")
    seed_state(journals={"f": {"status": "finished"}})
    assert bn.cmd_start(START, NOW) == ""
    assert bn.load_state()["journals"]["f"]["status"] == "open"


def test_finished_in_backlog_is_skipped(env):
    jr(env, "f", "2026-10-22")
    seed_state(journals={"f": {"status": "finished"}}, backlog={"slugs": ["f"], "next_offer_at": "2099-01-01T13:00:00"})
    assert bn.cmd_start(START, NOW) == ""


def test_stale_after_14_days(env):
    jr(env, "old", ago(15))
    seed_state(journals={"old": {"status": "snoozed", "last_asked": ago(15), "asked_at_entry": ago(15)}})
    out = bn.cmd_start(START, NOW)
    assert "`old`" in out
    assert "/brain-graduate old" in out
    assert "explicit yes" in out


def test_not_stale_at_13_days(env):
    jr(env, "young", ago(13))
    seed_state(journals={"young": {"status": "snoozed", "last_asked": ago(20)}})
    assert bn.cmd_start(START, NOW) == ""


def test_stale_needs_last_asked_14_days_ago(env):
    jr(env, "old", ago(30))
    seed_state(journals={"old": {"status": "snoozed", "last_asked": ago(5)}})
    assert bn.cmd_start(START, NOW) == ""


def test_stale_never_asked_open_journal_offers_oldest_first(env):
    jr(env, "newer", ago(16))
    jr(env, "oldest", ago(40))
    seed_state()
    assert "`oldest`" in bn.cmd_start(START, NOW)


def test_stale_skips_backlog_and_graduated(env):
    jr(env, "inback", ago(30))
    jr(env, "grad", ago(30), marker=ago(10))
    seed_state(backlog={"slugs": ["inback"], "next_offer_at": "2099-01-01T13:00:00"})
    assert bn.cmd_start(START, NOW) == ""


def test_daily_checks_run_once_a_day(env):
    jr(env, "old", ago(30))
    seed_state()
    assert "`old`" in bn.cmd_start(START, NOW)
    assert bn.load_state()["daily_stamp"] == TODAY.isoformat()
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW) == ""
    tomorrow = NOW + timedelta(days=1)
    bn.cmd_start({"session_id": "s3", "source": "startup"}, tomorrow)
    assert bn.load_state()["daily_stamp"] == tomorrow.date().isoformat()


def test_contribute_ignores_generated_files(env):
    make_shared(env)
    for name in ("index.md", "log.md", "wiki/MUSTKNOW.md"):
        (env.shared / name).write_text("changed\n")
    (env.shared / "newwiki").mkdir()
    (env.shared / "newwiki" / "MUSTKNOW.md").write_text("new\n")
    (env.shared / "index.md").write_text("changed again\n")
    seed_state()
    assert bn.shared_has_contribution(env.shared) is False
    assert bn.cmd_start(START, NOW) == ""


def test_contribute_on_tracked_page_change(env):
    make_shared(env)
    (env.shared / "wiki" / "page.md").write_text("changed\n")
    seed_state()
    out = bn.cmd_start(START, NOW)
    assert "/brain-contribute" in out
    assert "explicit yes" in out


def test_contribute_on_staged_change_and_rename(env):
    make_shared(env)
    sh(env.shared, "mv", "page.md", "moved.md")
    seed_state()
    assert bn.shared_has_contribution(env.shared) is True


def test_contribute_on_unpushed_commit(env):
    make_shared(env)
    (env.shared / "page.md").write_text("changed\n")
    sh(env.shared, "commit", "--quiet", "-am", "local")
    seed_state()
    assert "/brain-contribute" in bn.cmd_start(START, NOW)


def test_contribute_silent_after_push(env):
    make_shared(env)
    (env.shared / "page.md").write_text("changed\n")
    sh(env.shared, "commit", "--quiet", "-am", "local")
    sh(env.shared, "push", "--quiet", "origin", "master")
    assert bn.shared_has_contribution(env.shared) is False


def test_contribute_silent_without_origin_or_repo(env, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert bn.shared_has_contribution(plain) is False
    assert bn.shared_has_contribution(tmp_path / "nope") is False
    sh(plain, "init", "--quiet")
    sh(plain, "config", "user.email", "test@example.com")
    sh(plain, "config", "user.name", "Test")
    (plain / "a.md").write_text("x\n")
    sh(plain, "add", "-A")
    sh(plain, "commit", "--quiet", "-m", "x")
    assert bn.shared_has_contribution(plain) is False


def test_dry_feed_after_21_days(env):
    seed_state(first_run_at="2026-09-01T09:00:00")
    out = bn.cmd_start(START, NOW)
    assert "feed rules" in out
    assert "explicit yes" in out
    assert bn.load_state()["dry_feed_shown"] == TODAY.isoformat()


def test_dry_feed_waits_21_days_and_not_repeated(env):
    seed_state(first_run_at=(NOW - timedelta(days=20)).isoformat())
    assert bn.cmd_start(START, NOW) == ""
    seed_state(first_run_at="2026-09-01T09:00:00", dry_feed_shown=ago(10))
    assert bn.cmd_start(START, NOW) == ""
    seed_state(first_run_at="2026-09-01T09:00:00", dry_feed_shown=ago(21))
    assert "feed rules" in bn.cmd_start(START, NOW)


def test_dry_feed_silent_when_journals_exist(env):
    jr(env, "j", "2026-10-22", marker="2026-10-22")
    seed_state(first_run_at="2026-09-01T09:00:00")
    assert bn.cmd_start(START, NOW) == ""


def test_only_one_nudge_and_priority_order(env):
    make_shared(env)
    (env.shared / "page.md").write_text("changed\n")
    jr(env, "p", "2026-10-22")
    jr(env, "old", ago(30))
    jr(env, "inback", "2026-01-01")
    seed_state(pending_ask=["p"], journals={"f": {"status": "finished"}}, backlog={"slugs": ["inback"]})
    jr(env, "f", "2026-10-22")
    out = bn.cmd_start(START, NOW)
    assert "`p`" in out
    assert "/brain-contribute" not in out
    assert "backlog" not in out.lower()
    # Next start: finished beats stale, contribute and backlog.
    out = bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW)
    assert "/brain-graduate f" in out and "`old`" not in out


def test_contribute_beats_backlog_and_dry_feed(env):
    make_shared(env)
    (env.shared / "page.md").write_text("changed\n")
    jr(env, "inback", "2026-01-01")
    seed_state(first_run_at="2026-09-01T09:00:00", backlog={"slugs": ["inback"]})
    out = bn.cmd_start(START, NOW)
    assert "/brain-contribute" in out
    out = bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW)
    assert "/brain-graduate --backlog" in out


def test_non_dict_state_fields_count_as_empty(env):
    jr(env, "a", "2026-01-01")
    for bad in ([], "x", 5, None):
        seed_state(journals=bad, backlog=bad, sessions=bad, pending_ask=bad)
        bn.cmd_start(START, NOW)
    seed_state(journals={"p": "junk"}, backlog={"slugs": "junk"}, pending_ask=["p", 3, None])
    assert "`a`" in bn.cmd_start(START, NOW)  # only the stale rule matches


def test_corrupt_state_file_starts_fresh(env):
    jr(env, "a", "2026-01-01")
    bn.state_path().write_text("{broken")
    assert "/brain-graduate --backlog" in bn.cmd_start(START, NOW)


def test_texts_name_script_path_and_tail(env):
    jr(env, "p", "2026-10-22")
    seed_state(pending_ask=["p"])
    out = bn.cmd_start(START, NOW)
    assert str(Path(bn.__file__).resolve()) in out
    assert "drop the subject" in out
    for ch in ("\u2014", "\u2013", ";"):
        assert ch not in out


def parse_payload(r):
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    return payload["hookSpecificOutput"]["additionalContext"]


def test_cli_start_empty_stdin_emits_valid_json(env):
    jr(env, "a", "2026-01-01")
    assert parse_payload(run_cli(env, "start", stdin="")) == ""


def test_cli_start_invalid_stdin_emits_empty_payload(env):
    assert parse_payload(run_cli(env, "start", stdin="{not json")) == ""
    assert parse_payload(run_cli(env, "start", stdin="[1,2]")) == ""


def test_cli_start_emits_nudge(env):
    jr(env, "a", "2026-01-01")
    ctx = parse_payload(run_cli(env, "start", stdin=json.dumps(START)))
    assert "/brain-graduate --backlog" in ctx
    assert bn.load_state()["first_run_at"]


def test_cli_start_survives_exception(env, monkeypatch, capsys):
    def boom(hook, now):
        raise RuntimeError("boom")
    monkeypatch.setattr(bn, "cmd_start", boom)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("{}"))
    assert bn.main(["start"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["additionalContext"] == ""


# Fix round 1

def test_resume_offer_decline_is_honoured_same_day(env):
    jr(env, "left", "2026-01-01")
    seed_state(backlog={"slugs": ["left"], "dig_started_at": "2026-10-22T10:00:00"})
    assert "interrupted" in bn.cmd_start(START, NOW)
    run = run_cli(env, "answer", "--backlog", "not-now")
    assert run.returncode == 0
    assert bn.cmd_start({"session_id": "s2", "source": "clear"}, NOW + timedelta(minutes=5)) == ""
    assert bn.load_state()["backlog"]["dig_started_at"] is None


def test_unanswered_backlog_offer_counts_as_no(env):
    jr(env, "a", "2026-01-01")
    seed_state(backlog={"slugs": ["a"], "next_offer_at": "2026-10-23T13:00:00"})
    assert "/brain-graduate --backlog" in bn.cmd_start(START, datetime(2026, 10, 23, 13, 5))
    assert bn.load_state()["backlog"]["next_offer_at"] == "2026-10-30T13:00:00"
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, datetime(2026, 10, 23, 14)) == ""
    assert bn.cmd_start({"session_id": "s3", "source": "startup"}, datetime(2026, 10, 29, 14)) == ""
    assert "/brain-graduate --backlog" in bn.cmd_start({"session_id": "s4", "source": "startup"}, datetime(2026, 10, 30, 13))


def test_first_run_offer_unanswered_is_not_repeated(env):
    jr(env, "a", "2026-01-01")
    assert "/brain-graduate --backlog" in bn.cmd_start(START, NOW)
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW + timedelta(hours=1)) == ""


def test_resume_offer_unanswered_is_offered_once(env):
    jr(env, "left", "2026-01-01")
    seed_state(backlog={"slugs": ["left"], "dig_started_at": "2026-10-22T10:00:00"})
    assert "interrupted" in bn.cmd_start(START, NOW)
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW + timedelta(minutes=5)) == ""


def test_not_now_clears_dig_started():
    s = bn.answer_backlog_not_now({"backlog": {"dig_started_at": "2026-10-22T10:00:00"}}, datetime(2026, 10, 23, 15))
    assert s["backlog"]["dig_started_at"] is None


def test_compact_keeps_session_start(env):
    seed_state()
    bn.cmd_start(START, datetime(2026, 10, 23, 8))
    bn.cmd_start({"session_id": "s1", "source": "compact"}, NOW)
    assert bn.load_state()["sessions"]["s1"]["started_at"] == "2026-10-23T08:00:00"
    for source in ("startup", "resume", "clear"):
        bn.cmd_start({"session_id": "s1", "source": source}, NOW)
        assert bn.load_state()["sessions"]["s1"]["started_at"] == "2026-10-23T13:30:00"
        bn.cmd_start(START, datetime(2026, 10, 23, 8))


def test_compact_without_prior_record_uses_now(env):
    seed_state()
    bn.cmd_start({"session_id": "s9", "source": "compact"}, NOW)
    assert bn.load_state()["sessions"]["s9"]["started_at"] == "2026-10-23T13:30:00"


def test_finished_offer_once_a_day(env):
    jr(env, "f", "2026-10-22")
    seed_state(journals={"f": {"status": "finished"}})
    assert "/brain-graduate f" in bn.cmd_start(START, datetime(2026, 10, 23, 9))
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, datetime(2026, 10, 23, 10)) == ""
    assert "/brain-graduate f" in bn.cmd_start({"session_id": "s3", "source": "startup"}, datetime(2026, 10, 24, 9))


def test_pending_and_stale_emit_set_last_asked(env):
    jr(env, "p", "2026-10-22")
    jr(env, "old", ago(30))
    seed_state(pending_ask=["p"])
    bn.cmd_start(START, NOW)
    assert bn.load_state()["journals"]["p"]["last_asked"] == TODAY.isoformat()
    bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW)
    assert bn.load_state()["journals"]["old"]["last_asked"] == TODAY.isoformat()


def test_non_string_shared_path_is_unset(env):
    for bad in (5, ["x"], None, {"a": 1}):
        env.cfg.write_text(json.dumps({"journalsDir": str(env.journals), "sharedTree": {"path": bad}}))
        seed_state()
        assert bn.cmd_start(START, NOW) == ""
    env.cfg.write_text(json.dumps({"journalsDir": str(env.journals), "sharedTree": 7}))
    seed_state()
    assert bn.cmd_start(START, NOW) == ""


# Task 4: SessionEnd

def _set_mtime(path, when):
    os.utime(path, (when.timestamp(),) * 2)


def _seed_session(sid="s", started="2026-10-23T10:00:00", **kw):
    state = {"first_run_at": "2026-10-20T09:00:00", "sessions": {sid: {"started_at": started, "stop_fired_at": None}}}
    state.update(kw)
    bn.save_state(state)


def test_end_records_touched_journals(env):
    a = jr(env, "a", "2026-10-23")
    b = jr(env, "b", "2026-10-01")
    _set_mtime(a, datetime(2026, 10, 23, 11, 0))
    _set_mtime(b, datetime(2026, 10, 23, 9, 0))
    _seed_session()
    bn.cmd_end({"session_id": "s"})
    assert bn.load_state()["pending_ask"] == ["a"]


def test_end_appends_without_duplicates_and_keeps_order(env):
    for slug in ("a", "b"):
        _set_mtime(jr(env, slug, "2026-10-23"), datetime(2026, 10, 23, 11, 0))
    _seed_session(pending_ask=["z", "b"])
    bn.cmd_end({"session_id": "s"})
    assert bn.load_state()["pending_ask"] == ["z", "b", "a"]


def test_end_without_session_record_is_noop(env):
    _set_mtime(jr(env, "a", "2026-10-23"), datetime(2026, 10, 23, 11, 0))
    _seed_session()
    before = bn.state_path().read_text()
    bn.cmd_end({"session_id": "other"})
    bn.cmd_end({})
    assert bn.state_path().read_text() == before


def test_end_with_unparseable_started_at_is_noop(env):
    _set_mtime(jr(env, "a", "2026-10-23"), datetime(2026, 10, 23, 11, 0))
    _seed_session(started="garbage")
    before = bn.state_path().read_text()
    bn.cmd_end({"session_id": "s"})
    assert bn.state_path().read_text() == before


def test_end_tolerates_malformed_state(env):
    _set_mtime(jr(env, "a", "2026-10-23"), datetime(2026, 10, 23, 11, 0))
    bn.save_state({"sessions": {"s": "oops"}, "pending_ask": "str"})
    bn.cmd_end({"session_id": "s"})
    bn.save_state({"sessions": {"s": {"started_at": "2026-10-23T10:00:00"}}, "pending_ask": "str"})
    bn.cmd_end({"session_id": "s"})
    assert bn.load_state()["pending_ask"] == ["a"]


def test_end_is_fast_on_200_journals(env):
    import time
    for i in range(200):
        (env.journals / f"j{i}.md").write_text("## 2026-01-01\n")
    _seed_session("s", "2026-01-01T00:00:00")
    t = time.monotonic()
    r = run_cli(env, "end", stdin=json.dumps({"session_id": "s"}))
    assert r.returncode == 0
    assert time.monotonic() - t < 1.0
    assert len(bn.load_state()["pending_ask"]) == 200


def test_cli_end_empty_stdin_exits_zero(env):
    for stdin in ("", "   ", "not json", "[1]"):
        r = run_cli(env, "end", stdin=stdin)
        assert r.returncode == 0 and r.stdout == ""


# Task 5: Stop

def tool(name, **inp):
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t", "name": name, "input": inp}]}})


def edit(path):
    return tool("Edit", file_path=str(path), old_string="a", new_string="b")


def bash(cmd):
    return tool("Bash", command=cmd)


def transcript(env, *lines, name="t.jsonl"):
    path = env.root / name
    path.write_text("\n".join(lines) + "\n")
    return path


def stop_hook(env, path, **kw):
    return {"session_id": "s", "transcript_path": str(path), "cwd": str(env.root), "stop_hook_active": False, **kw}


def three_edits(env):
    return [edit(env.root / f"f{i}.py") for i in range(3)]


def test_scan_transcript_events_and_index(env):
    path = transcript(
        env,
        edit(env.journals / "a.md"),
        bash("ls"),
        edit(env.root / "x.py"),
        tool("Write", file_path=str(env.root / "y.py"), content=""),
        tool("NotebookEdit", notebook_path=str(env.journals / "n.md")),
        bash("git commit -m x"),
        bash("git push origin main"),
        bash("gh issue create --title x"),
        bash("gh issue view 1"),
        bash("tea issues create --title x"),
        bash("tea pulls list"),
        tool("mcp__other__create_thing"),
    )
    events, last = bn.scan_transcript(path, env.journals)
    assert events == [(0, "journal"), (2, "edit"), (3, "edit"), (4, "journal"), (5, "commit"),
                      (6, "commit"), (7, "remote_write"), (9, "remote_write")]
    assert last == 11


def test_scan_transcript_missing_file_is_empty(env):
    assert bn.scan_transcript(env.root / "nope.jsonl", env.journals) == ([], -1)


def test_stop_fires_after_three_edits(env):
    path = transcript(env, *three_edits(env))
    out = bn.cmd_stop(stop_hook(env, path))
    assert out["decision"] == "block"
    assert str(env.journals) in out["reason"]
    assert bn.load_state()["sessions"]["s"]["stop_fired_at"] == 2


def test_stop_reason_follows_spec_wording_and_prose_rules(env):
    out = bn.cmd_stop(stop_hook(env, transcript(env, *three_edits(env))))
    reason = out["reason"]
    assert reason.startswith("Before you hand back: this session did substantive work since the journal was last updated.")
    assert "append a dated entry with the results" in reason
    assert "say so in one line and stop" in reason
    assert reason.endswith("Do not mention this check otherwise.")
    for bad in ("—", "–", ";"):
        assert bad not in reason


def test_stop_fires_after_one_commit(env):
    assert bn.cmd_stop(stop_hook(env, transcript(env, bash("git commit -am x"))))["decision"] == "block"


def test_stop_counts_remote_writes(env):
    lines = [bash("gh issue comment 1 --body x")] * 2 + [bash("tea issues edit 1 --title y")]
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines)))["decision"] == "block"


def test_stop_silent_below_threshold(env):
    path = transcript(env, edit(env.root / "a.py"), edit(env.root / "b.py"), bash("ls"), edit(env.journals / "j.md"))
    assert bn.cmd_stop(stop_hook(env, path)) is None
    assert bn.load_state() == {}


def test_stop_silent_when_stop_hook_active(env):
    path = transcript(env, *three_edits(env))
    assert bn.cmd_stop(stop_hook(env, path, stop_hook_active=True)) is None


def test_stop_silent_without_journals_dir(env):
    env.cfg.write_text(json.dumps({"sharedTree": {"path": str(env.shared)}}))
    assert bn.cmd_stop(stop_hook(env, transcript(env, *three_edits(env)))) is None


def test_stop_silent_without_session_id_or_transcript(env):
    path = transcript(env, *three_edits(env))
    assert bn.cmd_stop({"transcript_path": str(path), "cwd": str(env.root)}) is None
    assert bn.cmd_stop({"session_id": "s", "cwd": str(env.root)}) is None
    assert bn.cmd_stop({"session_id": "s", "transcript_path": str(env.root / "nope"), "cwd": str(env.root)}) is None
    assert bn.cmd_stop("junk") is None


def test_stop_fires_once_per_stretch(env):
    path = transcript(env, *three_edits(env))
    assert bn.cmd_stop(stop_hook(env, path)) is not None
    assert bn.cmd_stop(stop_hook(env, path)) is None
    path = transcript(env, *three_edits(env), *three_edits(env))
    assert bn.cmd_stop(stop_hook(env, path)) is None


def test_stop_rearms_after_journal_write(env):
    lines = three_edits(env)
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines))) is not None
    lines += [edit(env.journals / "j.md")]
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines))) is None
    lines += three_edits(env)
    out = bn.cmd_stop(stop_hook(env, transcript(env, *lines)))
    assert out["decision"] == "block"
    assert bn.load_state()["sessions"]["s"]["stop_fired_at"] == 6


def test_stop_counts_only_after_last_journal_write(env):
    lines = three_edits(env) + [edit(env.journals / "j.md"), edit(env.root / "a.py"), edit(env.root / "b.py")]
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines))) is None
    lines.append(edit(env.root / "c.py"))
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines))) is not None


def test_stop_keeps_existing_session_start(env):
    bn.save_state({"sessions": {"s": {"started_at": "2026-10-23T09:00:00", "stop_fired_at": None}}})
    bn.cmd_stop(stop_hook(env, transcript(env, *three_edits(env))))
    sess = bn.load_state()["sessions"]["s"]
    assert sess == {"started_at": "2026-10-23T09:00:00", "stop_fired_at": 2}


def test_stop_creates_session_entry_with_valid_started_at(env):
    bn.cmd_stop(stop_hook(env, transcript(env, *three_edits(env))))
    sess = bn.load_state()["sessions"]["s"]
    assert datetime.fromisoformat(sess["started_at"]).tzinfo is None
    bn.save_state({"sessions": {"s": {"started_at": "garbage"}}})
    bn.cmd_stop(stop_hook(env, transcript(env, *three_edits(env))))
    sess = bn.load_state()["sessions"]["s"]
    assert datetime.fromisoformat(sess["started_at"]).tzinfo is None
    assert sess["stop_fired_at"] == 2


def test_stop_silent_in_linked_worktree(env):
    main = env.root / "main"
    main.mkdir()
    subprocess.run(["git", "init", "--quiet", str(main)], check=True)
    sh(main, "config", "user.email", "t@example.com")
    sh(main, "config", "user.name", "T")
    (main / "f").write_text("x")
    sh(main, "add", "-A")
    sh(main, "commit", "--quiet", "-m", "init")
    wt = env.root / "wt"
    sh(main, "worktree", "add", "--quiet", "-b", "lane", str(wt))
    path = transcript(env, *three_edits(env))
    assert bn.cmd_stop(stop_hook(env, path, cwd=str(wt))) is None
    assert bn.cmd_stop(stop_hook(env, path, cwd=str(wt / "."))) is None
    assert bn.load_state() == {}
    # The main checkout and a non-repo cwd both still fire.
    assert bn.cmd_stop(stop_hook(env, path, cwd=str(main))) is not None


def test_stop_fires_in_non_repo_cwd(env):
    assert bn.cmd_stop(stop_hook(env, transcript(env, *three_edits(env)))) is not None


def test_stop_skips_corrupt_transcript_line(env):
    lines = [edit(env.root / "a.py"), "{not json", "[1, 2]", "null", '{"type": "assistant", "message": "x"}',
             json.dumps({"type": "assistant", "message": {"content": "text"}}),
             json.dumps({"type": "assistant", "message": {"content": [None, 3, {"type": "tool_use"}]}}),
             edit(env.root / "b.py"), edit(env.root / "c.py"), '{"type": "assistant", "mess']
    path = env.root / "t.jsonl"
    path.write_text("\n".join(lines))
    out = bn.cmd_stop(stop_hook(env, path))
    assert out["decision"] == "block"


def test_bash_heredoc_into_journal_counts_as_journal(env):
    j = env.journals / "j.md"
    lines = three_edits(env) + [bash(f"cat >> {j} <<'EOF'\n## 2026-10-23\nEOF")]
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines))) is None
    for cmd in (f"echo x | tee {j}", f"sed -i '' s/a/b/ {j}", f"echo x > {j}"):
        events, _ = bn.scan_transcript(transcript(env, bash(cmd)), env.journals)
        assert events == [(0, "journal")]
    for cmd in (f"cat {j}", "echo x > /tmp/other"):
        events, _ = bn.scan_transcript(transcript(env, bash(cmd)), env.journals)
        assert events == []


def test_stop_journal_path_is_symlink_safe(env):
    link = env.root / "link"
    link.symlink_to(env.journals)
    events, _ = bn.scan_transcript(transcript(env, edit(link / "j.md")), env.journals)
    assert events == [(0, "journal")]


def test_cli_stop_emits_block_payload(env):
    path = transcript(env, *three_edits(env))
    r = run_cli(env, "stop", stdin=json.dumps(stop_hook(env, path)))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["decision"] == "block" and str(env.journals) in out["reason"]
    r = run_cli(env, "stop", stdin=json.dumps(stop_hook(env, path)))
    assert json.loads(r.stdout) == {}


def test_cli_stop_empty_stdin_emits_empty_object(env):
    for stdin in ("", "  ", "not json", "[1]", "{}", json.dumps({"session_id": "s", "transcript_path": 5})):
        r = run_cli(env, "stop", stdin=stdin)
        assert r.returncode == 0
        assert json.loads(r.stdout) == {}


def test_cli_stop_swallows_exceptions(env, monkeypatch, capsys):
    def boom(hook, now=None):
        raise RuntimeError("x")
    monkeypatch.setattr(bn, "cmd_stop", boom)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("{}"))
    assert bn.cmd_stop_cli() == 0
    assert json.loads(capsys.readouterr().out) == {}


# Fix round 1: journal writes in every path spelling

def test_stop_silent_with_two_edits_and_no_journal_write(env):
    path = transcript(env, edit(env.root / "a.py"), edit(env.root / "b.py"))
    assert bn.cmd_stop(stop_hook(env, path)) is None


@pytest.mark.parametrize("spelling", ["~/journals", "$HOME/journals", "${HOME}/journals", '"$HOME/journals', "'~/journals"])
def test_bash_journal_write_in_home_spellings(env, monkeypatch, spelling):
    monkeypatch.setenv("HOME", str(env.root))
    cmd = f"cat >> {spelling}/x.md <<'EOF'\n## 2026-10-23\nEOF"
    events, _ = bn.scan_transcript(transcript(env, bash(cmd)), env.journals)
    assert events == [(0, "journal")]


def test_bash_journal_write_literal_configured_and_real_spelling(env, monkeypatch):
    link = env.root / "link"
    link.symlink_to(env.journals)
    env.cfg.write_text(json.dumps({"journalsDir": str(link)}))
    cfg = bn.load_config()
    for cmd in (f"echo x >> {link}/a.md", f"echo x >> {env.journals.resolve()}/a.md"):
        events, _ = bn.scan_transcript(transcript(env, bash(cmd)), bn.journals_dir(cfg), cfg["journalsDir"])
        assert events == [(0, "journal")]


def test_bash_write_operators_must_sit_before_the_path(env):
    j = env.journals
    reads = [f"grep x {j}/a.md 2>/dev/null", f"cat {j}/a.md 2>&1", f"ls {j}/ > /dev/null", f"sed s/a/b/ {j}/a.md",
             f"sed -n p {j}/a.md > /tmp/out", f"echo {j}/a.md | tee /tmp/out"]
    for cmd in reads:
        events, _ = bn.scan_transcript(transcript(env, bash(cmd)), j)
        assert events == [], cmd
    writes = [f"tee -a {j}/a.md", f"echo x | tee {j}/a.md", f'echo x > "{j}/a.md"', f"echo x >{j}/a.md",
              f"sed -i '' s/a/b/ {j}/a.md", f"sed -i.bak s/a/b/ {j}/a.md"]
    for cmd in writes:
        events, _ = bn.scan_transcript(transcript(env, bash(cmd)), j)
        assert events == [(0, "journal")], cmd


def test_stop_rearms_after_home_spelled_bash_journal_write(env, monkeypatch):
    monkeypatch.setenv("HOME", str(env.root))
    lines = three_edits(env)
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines))) is not None
    lines.append(bash("cat >> ~/journals/x.md <<'EOF'\n## 2026-10-23\nEOF"))
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines))) is None
    lines += three_edits(env)
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines)))["decision"] == "block"


def test_one_write_verb_set_for_every_tracker_cli(env):
    writes = ["gh pr create --fill", "gh pr merge 1", "gh issue comment 1 --body x", "gh pr reopen 1",
              "gh release create v1", "tea pulls create", "tea issues edit 1", "tea comment 1 x"]
    for cmd in writes:
        assert bn.scan_transcript(transcript(env, bash(cmd)), env.journals)[0] == [(0, "remote_write")], cmd
    for cmd in ("gh pr list", "gh pr view 1", "gh issue view 1", "gh pr diff 1", "tea issues list", "ghx pr create"):
        assert bn.scan_transcript(transcript(env, bash(cmd)), env.journals)[0] == [], cmd



def snapshot():
    return json.dumps(bn.load_state(), sort_keys=True)


# F1: headless sessions

def test_start_off_records_session_and_changes_nothing_else(env, monkeypatch):
    monkeypatch.setenv("BRAIN_NUDGE_OFF", "1")
    jr(env, "a", "2026-01-01")
    assert bn.cmd_start(START, NOW) == ""
    state = bn.load_state()
    assert set(state) == {"sessions"}
    assert state["sessions"]["s1"]["started_at"] == NOW.isoformat(timespec="seconds")


def test_start_off_keeps_pending_ask_stamp_and_backlog_offer(env, monkeypatch):
    monkeypatch.setenv("BRAIN_NUDGE_OFF", "1")
    jr(env, "a", "2026-01-01")
    jr(env, "p", "2026-10-22")
    seed_state(pending_ask=["p"], daily_stamp="2026-10-22",
               backlog={"slugs": ["a"], "next_offer_at": "2026-10-23T13:00:00"})
    before = bn.load_state()
    assert bn.cmd_start(START, NOW) == ""
    after = bn.load_state()
    assert after.pop("sessions")["s1"]["started_at"]
    assert after == before


def test_start_off_only_for_value_one(env, monkeypatch):
    monkeypatch.setenv("BRAIN_NUDGE_OFF", "0")
    jr(env, "a", "2026-01-01")
    assert "/brain-graduate --backlog" in bn.cmd_start(START, NOW)


def test_stop_and_end_off_do_nothing(env, monkeypatch):
    monkeypatch.setenv("BRAIN_NUDGE_OFF", "1")
    path = transcript(env, *three_edits(env))
    assert bn.cmd_stop(stop_hook(env, path)) in ({}, None)
    assert not bn.state_path().exists()
    a = jr(env, "a", "2026-10-23")
    _set_mtime(a, datetime(2026, 10, 23, 11, 0))
    _seed_session()
    before = snapshot()
    bn.cmd_end({"session_id": "s"})
    assert snapshot() == before


def test_cli_off_emits_empty_payloads(env, monkeypatch):
    monkeypatch.setenv("BRAIN_NUDGE_OFF", "1")
    jr(env, "a", "2026-01-01")
    r = run_cli(env, "start", stdin=json.dumps(START))
    assert parse_payload(r) == ""
    path = transcript(env, *three_edits(env))
    r = run_cli(env, "stop", stdin=json.dumps(stop_hook(env, path)))
    assert json.loads(r.stdout) == {}
    assert "first_run_at" not in bn.load_state()


# F3: resume only after an interrupted dig

def test_backlog_start_stores_dig_session_and_clears_interrupted(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "dig1")
    s = bn.backlog_start({"backlog": {"slugs": ["a"], "interrupted": True}}, NOW)
    assert s["backlog"]["dig_session"] == "dig1"
    assert s["backlog"]["interrupted"] is False
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert bn.backlog_start({}, NOW)["backlog"]["dig_session"] is None


def running_dig(env, **kw):
    jr(env, "left", "2026-01-01")
    backlog = {"slugs": ["left"], "next_offer_at": "2026-12-01T13:00:00",
               "dig_started_at": "2026-10-23T12:00:00", "dig_session": "dig1"}
    backlog.update(kw)
    seed_state(backlog=backlog, sessions={"dig1": {"started_at": "2026-10-23T12:00:00", "stop_fired_at": None}})


def test_second_session_during_running_dig_gets_no_resume_offer(env):
    running_dig(env)
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW) == ""


def test_dig_session_end_with_backlog_left_marks_interrupted(env):
    running_dig(env)
    bn.cmd_end({"session_id": "dig1"})
    assert bn.load_state()["backlog"]["interrupted"] is True
    out = bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW)
    assert "interrupted" in out and "/brain-graduate --backlog" in out
    backlog = bn.load_state()["backlog"]
    assert backlog["interrupted"] is False
    assert backlog["dig_started_at"] is None and backlog["dig_session"] is None
    assert bn.cmd_start({"session_id": "s3", "source": "startup"}, NOW + timedelta(minutes=5)) == ""


def test_other_session_end_does_not_interrupt(env):
    running_dig(env)
    bn.cmd_end({"session_id": "other"})
    assert not bn.load_state()["backlog"].get("interrupted")


def test_dig_session_end_with_empty_backlog_clears_fields(env):
    jr(env, "dug", "2026-01-01", marker="2026-10-23")
    seed_state(backlog={"slugs": ["dug"], "dig_started_at": "2026-10-23T12:00:00", "dig_session": "dig1",
                        "interrupted": False},
               sessions={"dig1": {"started_at": "2026-10-23T12:00:00", "stop_fired_at": None}})
    bn.cmd_end({"session_id": "dig1"})
    backlog = bn.load_state()["backlog"]
    assert backlog["dig_started_at"] is None and backlog["dig_session"] is None
    assert not backlog["interrupted"]
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW) == ""


def test_null_dig_session_keeps_old_resume_rule(env):
    running_dig(env, dig_session=None)
    assert "interrupted" in bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW)


def test_cli_backlog_start_uses_session_env(env, monkeypatch):
    jr(env, "a", "2026-01-01")
    seed_state(backlog={"slugs": ["a"]})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "dig9")
    assert run_cli(env, "backlog", "start").returncode == 0
    assert bn.load_state()["backlog"]["dig_session"] == "dig9"


# F4, F7: contribute counts new pages and takes no git locks

def test_contribute_counts_new_untracked_page(env):
    make_shared(env)
    (env.shared / "wiki" / "fresh.md").write_text("new\n")
    seed_state()
    assert bn.shared_has_contribution(env.shared) is True
    assert "/brain-contribute" in bn.cmd_start(START, NOW)


def test_contribute_ignores_untracked_non_pages_and_ignored_files(env):
    make_shared(env)
    (env.shared / ".gitignore").write_text("secret.md\n")
    sh(env.shared, "add", ".gitignore")
    sh(env.shared, "commit", "--quiet", "-m", "ignore")
    sh(env.shared, "push", "--quiet")
    (env.shared / "secret.md").write_text("x\n")
    (env.shared / "notes.txt").write_text("x\n")
    seed_state()
    assert bn.shared_has_contribution(env.shared) is False


def test_contribute_check_uses_no_optional_locks(env, monkeypatch):
    make_shared(env)
    (env.shared / "wiki" / "fresh.md").write_text("new\n")
    calls = []
    real = subprocess.run

    def spy(args, **kw):
        calls.append(args)
        return real(args, **kw)
    monkeypatch.setattr(bn.subprocess, "run", spy)
    bn.shared_has_contribution(env.shared)
    assert len(calls) >= 2
    assert all(c[:2] == ["git", "--no-optional-locks"] for c in calls)


# F6: pending ask once a day

def test_pending_ask_skipped_when_asked_today(env):
    jr(env, "p", "2026-10-22")
    seed_state(pending_ask=["p"], journals={"p": {"last_asked": TODAY.isoformat()}})
    assert bn.cmd_start(START, NOW) == ""


def test_pending_ask_fires_when_last_asked_yesterday(env):
    jr(env, "p", "2026-10-22")
    seed_state(pending_ask=["p"], journals={"p": {"last_asked": ago(1)}})
    assert "`p`" in bn.cmd_start(START, NOW)


# F8: the brain skills' own vault writes are not work

def test_vault_writes_do_not_count_as_edits(env, monkeypatch):
    private = env.root / "private"
    private.mkdir()
    link = env.root / "link"
    link.symlink_to(env.shared)
    cfg = json.loads(env.cfg.read_text())
    cfg["privateVault"] = {"path": str(private)}
    env.cfg.write_text(json.dumps(cfg))
    roots = [str(env.root / "personal"), str(link), str(private)]
    path = transcript(
        env,
        edit(env.root / "personal" / "wiki" / "a.md"),
        edit(env.shared / "wiki" / "b.md"),
        tool("Write", file_path=str(private / "c.md"), content=""),
        tool("NotebookEdit", notebook_path=str(link / "d.ipynb")),
        edit(env.root / "code.py"),
    )
    events, _ = bn.scan_transcript(path, env.journals, None, roots)
    assert events == [(4, "edit")]
    vault_only = transcript(env, *[edit(env.shared / "wiki" / f"p{i}.md") for i in range(4)], name="v.jsonl")
    assert bn.cmd_stop(stop_hook(env, vault_only)) in ({}, None)


def test_vault_roots_expand_tilde(env, monkeypatch):
    home = env.root / "home"
    (home / "vault").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    path = transcript(env, edit(home / "vault" / "x.md"))
    assert bn.scan_transcript(path, env.journals, None, ["~/vault"])[0] == []


def test_stop_counts_work_outside_vault_roots(env):
    lines = three_edits(env) + [edit(env.root / "personal" / "p.md")]
    assert bn.cmd_stop(stop_hook(env, transcript(env, *lines)))["decision"] == "block"


# F9: lock around state writes

def test_sequential_cli_writers_both_land(env):
    (env.journals / "p.md").write_text("# P\n## 2026-10-20\n")
    assert run_cli(env, "answer", "p", "not-finished").returncode == 0
    assert run_cli(env, "answer", "--backlog", "not-now").returncode == 0
    state = bn.load_state()
    assert state["journals"]["p"]["status"] == "snoozed"
    assert "next_offer_at" in state["backlog"]
    assert (bn.state_path().parent / "nudge-state.lock").exists()


def test_start_takes_the_lock_file(env):
    jr(env, "a", "2026-01-01")
    bn.cmd_start(START, NOW)
    assert (bn.state_path().parent / "nudge-state.lock").exists()


def test_held_lock_times_out_and_writer_proceeds(env, monkeypatch):
    import fcntl
    monkeypatch.setattr(bn, "LOCK_WAIT", 0.2)
    lock = bn.state_path().parent / "nudge-state.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        (env.journals / "p.md").write_text("# P\n## 2026-10-20\n")
        start = datetime.now()
        assert run_cli_inproc_answer(env) == 0
        assert (datetime.now() - start).total_seconds() < 3
    assert bn.load_state()["journals"]["p"]["status"] == "finished"


def run_cli_inproc_answer(env):
    return bn.main(["answer", "p", "finished"])


# Weekly agent notice: an install from before the nudges has a wrapper without BRAIN_NUDGE_OFF

def weekly(env, wrapper_text, opted_in=True):
    cfg = json.loads(env.cfg.read_text())
    cfg["wiring"] = {"launchdWeekly": opted_in}
    env.cfg.write_text(json.dumps(cfg))
    wrapper = env.cfg.parent / "brain-maintenance"
    if wrapper_text is not None:
        wrapper.write_text(wrapper_text)
    return wrapper


OLD_WRAPPER = "#!/bin/zsh\nexport CLAUDE_VAULT=x\n"


def test_weekly_agent_notice_when_wrapper_lacks_variable(env):
    weekly(env, OLD_WRAPPER)
    seed_state()
    out = bn.cmd_start(START, NOW)
    assert "weekly maintenance agent" in out
    assert "`/brain-init`" in out and "explicit yes" in out
    assert bn.load_state()["weekly_agent_notice"] == TODAY.isoformat()


def test_weekly_agent_notice_silent_when_wrapper_current(env):
    weekly(env, OLD_WRAPPER + "export BRAIN_NUDGE_OFF=1\n")
    seed_state()
    assert bn.cmd_start(START, NOW) == ""
    assert "weekly_agent_notice" not in bn.load_state()


def test_weekly_agent_notice_silent_when_opted_out_or_file_missing(env):
    weekly(env, OLD_WRAPPER, opted_in=False)
    seed_state()
    assert bn.cmd_start(START, NOW) == ""
    (env.cfg.parent / "brain-maintenance").unlink()
    weekly(env, None)
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW) == ""
    assert "weekly_agent_notice" not in bn.load_state()


def test_weekly_agent_notice_silent_on_unreadable_wrapper(env):
    wrapper = weekly(env, None)
    wrapper.mkdir()  # reading a directory raises
    seed_state()
    assert bn.cmd_start(START, NOW) == ""


def test_weekly_agent_notice_once_a_day(env):
    weekly(env, OLD_WRAPPER)
    seed_state()
    assert "weekly maintenance agent" in bn.cmd_start(START, NOW)
    assert bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW) == ""
    tomorrow = NOW + timedelta(days=1)
    assert "weekly maintenance agent" in bn.cmd_start({"session_id": "s3", "source": "startup"}, tomorrow)


def test_weekly_agent_notice_not_on_compact_or_when_nudges_off(env, monkeypatch):
    weekly(env, OLD_WRAPPER)
    seed_state()
    assert bn.cmd_start({"session_id": "s1", "source": "compact"}, NOW) == ""
    monkeypatch.setenv("BRAIN_NUDGE_OFF", "1")
    assert bn.cmd_start(START, NOW) == ""
    assert "weekly_agent_notice" not in bn.load_state()


def test_weekly_agent_notice_beats_other_nudges_and_keeps_pending_ask(env):
    weekly(env, OLD_WRAPPER)
    jr(env, "old", ago(30))
    seed_state(pending_ask=["old"])
    out = bn.cmd_start(START, NOW)
    assert "weekly maintenance agent" in out
    assert bn.load_state()["pending_ask"] == ["old"]
    # The other nudge was displaced, not lost: it comes at the next start.
    assert "`old`" in bn.cmd_start({"session_id": "s2", "source": "startup"}, NOW)


def test_first_run_backlog_offer_beats_weekly_agent_notice(env):
    weekly(env, OLD_WRAPPER)
    jr(env, "a", "2026-01-01")
    assert "/brain-graduate --backlog" in bn.cmd_start(START, NOW)
    assert "weekly_agent_notice" not in bn.load_state()
