import importlib.util
from pathlib import Path


import yaml


SKILLS = Path(__file__).parent.parent / "skills"
EXPECTED_MODELS = {
    "brain-capture": "sonnet",
    "brain-contribute": "sonnet",
    "brain-sync": "sonnet",
    "brain-lint": "sonnet",
    "brain-digest": "sonnet",
    "brain-init": "sonnet",
    "brain-graduate": "opus",
    "brain-freshness": "opus",
}


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---", 2)[1]), text


def test_skill_frontmatter_and_models():
    paths = sorted(SKILLS.glob("*/SKILL.md"))
    assert {path.parent.name for path in paths} == set(EXPECTED_MODELS)
    for path in paths:
        frontmatter, _text = _frontmatter(path)
        assert isinstance(frontmatter, dict)
        assert {"name", "description", "model"} <= frontmatter.keys()
        assert frontmatter["model"] in {"sonnet", "opus"}
        assert frontmatter["model"] == EXPECTED_MODELS[path.parent.name]


def test_first_run_check_is_on_all_non_init_skills():
    paths = sorted(SKILLS.glob("*/SKILL.md"))
    carrying = []
    for path in paths:
        _fm, text = _frontmatter(path)
        if "First-run check" in text:
            carrying.append(path.parent.name)
    assert len(carrying) == 7
    assert "brain-init" not in carrying


def test_graduate_honours_journals_dir():
    text = (SKILLS / "brain-graduate" / "SKILL.md").read_text()
    assert "journalsDir" in text
    assert "--backlog" in text
    assert "backlog start" in text and "backlog list" in text
    assert "graduated_to" in text


def test_graduate_description_under_limit():
    fm, _ = _frontmatter(SKILLS / "brain-graduate" / "SKILL.md")
    assert len(fm["description"]) <= 1024


def _graduate():
    return (SKILLS / "brain-graduate" / "SKILL.md").read_text()


def test_graduate_single_slug_writes_empty_marker_when_nothing_durable():
    text = _graduate()
    step2 = text.split("2. Separate DURABLE")[1].split("3. Classify")[0]
    assert "empty `graduated_to=`" in step2
    assert "struck" in text.split("5. Update the marker")[1].split("6. Rebuild")[0]


def test_graduate_backlog_ranks_from_primer_lines_only():
    text = _graduate().split("## Backlog mode")[1]
    rank = text.split("2. Rank")[1].split("3. Dispatch")[0]
    assert "slug" in rank and "H1" in rank and "Primer" in rank and "15 lines" in rank
    assert "Never read whole journals" in rank
    assert "classifies each as work or private" in text.split("3. Dispatch")[1].split("4. Merge")[0]


def test_graduate_backlog_privacy_when_private_vault_unset():
    text = _graduate().split("## Backlog mode")[1]
    assert "privateVault.path" in text and "is not configured" in text
    assert "writes no pages" in text


def test_graduate_marker_wording_covers_newest_entry():
    text = _graduate()
    assert "already carry a marker" not in text
    assert "whose marker covers their newest entry are skipped" in text


VALIDATE = 'uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" validate'


def test_graduate_single_flow_validates_before_save():
    step8 = _graduate().split("8. After the rebuild")[1].split("## Backlog mode")[0]
    assert VALIDATE in step8
    assert '"ok": false' in step8
    assert step8.index(VALIDATE) < step8.index("brain_git.sh")


def test_graduate_backlog_flow_validates_before_save():
    step6 = _graduate().split("## Backlog mode")[1].split("6. Rebuild and log")[1].split("7. Run")[0]
    assert VALIDATE in step6
    assert '"ok": false' in step6
    assert step6.index(VALIDATE) < step6.index("save")


def test_graduate_names_closed_freshness_dimensions():
    step3 = _graduate().split("3. Classify")[1].split("4. NON-DESTRUCTIVE")[0]
    spec = importlib.util.spec_from_file_location("brain_check", SKILLS.parent / "scripts" / "brain_check.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    for dim in bc.FRESHNESS_DIMENSIONS:
        assert f"`{dim}`" in step3, dim
    assert "freshness_checked" in step3


def test_graduate_prose_has_no_banned_punctuation_in_new_text():
    text = _graduate()
    assert "\u2014" not in text and "\u2013" not in text


def _init_step(number):
    text = (SKILLS / "brain-init" / "SKILL.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith(f"{number}. ")]
    assert len(lines) == 1
    return lines[0]


def test_init_step_7_refreshes_the_agent_when_already_opted_in():
    step = _init_step(7)
    assert "install_launchd(vault_path=parent)" in step
    assert "wiring.launchdWeekly" in step and "already true" in step
    assert "re-run refreshes them" in step
    assert "When they decline, do not call `install_launchd`" in step


def _init_section(title):
    text = (SKILLS / "brain-init" / "SKILL.md").read_text(encoding="utf-8")
    start = text.index(f"## {title}")
    return text[start:text.index("\n## ", start + 1)]


def test_init_detect_refreshes_the_agent_on_a_plain_rerun():
    detect = _init_section("1. Detect")
    assert "install_launchd(vault_path=" in detect
    assert "wiring.launchdWeekly" in detect and "true" in detect
    assert "before" in detect and "report the current config" in detect
    assert "refreshed" in detect and "installed plugin version" in detect
    assert "ask what to change" in detect
    assert "declined" in detect
