import importlib.util
import json
from pathlib import Path

import pytest


HERE = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location("brain_lib", HERE / "scripts" / "brain_lib.py")
bl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bl)


def test_vault_resolution_uses_config_and_ignores_the_environment(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "personalVault": {"path": str(tmp_path / "personal")},
        "sharedTree": {"path": str(tmp_path / "shared")},
    }))
    monkeypatch.setattr(bl, "CONFIG_PATH", config)
    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path / "environment"))

    assert bl._resolve_vault(None) == tmp_path
    assert bl._resolve_vault(str(tmp_path / "explicit")) == tmp_path / "explicit"

    config.unlink()
    with pytest.raises(bl.ConfigError, match="Run /brain-init"):
        bl._resolve_vault(None)
    with pytest.raises(bl.ConfigError, match="Run /brain-init"):
        bl._resolve_vault(str(tmp_path / "explicit"))


def test_append_log_uses_the_personal_root(tmp_path, monkeypatch):
    personal = tmp_path / "personal"
    shared = tmp_path / "shared"
    personal.mkdir()
    shared.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "personalVault": {"path": str(personal)},
        "sharedTree": {"path": str(shared)},
    }))
    monkeypatch.setattr(bl, "CONFIG_PATH", config)

    bl.append_log(tmp_path / "tier", "first", "configured")
    configured_log = personal / "log.md"
    assert configured_log.exists()
    assert not (tmp_path / "tier" / "log.md").exists()
    assert "FIRST" in configured_log.read_text()

    assert bl.main(["log", "cli", "configured"]) == 0
    assert "CLI" in configured_log.read_text()

    config.unlink()
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    with pytest.raises(bl.ConfigError, match="Run /brain-init"):
        bl.append_log(fallback, "second", "no config")
    assert not (fallback / "log.md").exists()


def test_cli_without_config_exits_with_run_brain_init(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bl, "CONFIG_PATH", tmp_path / "absent.json")
    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path))
    for argv in (["rebuild"], ["rebuild", str(tmp_path)], ["find", "x"], ["find", "x", str(tmp_path)],
                 ["log", "op", "detail"]):
        assert bl.main(argv) == 1
        assert "Run /brain-init" in capsys.readouterr().err
    assert not (tmp_path / "index.md").exists() and not (tmp_path / "log.md").exists()


def test_scan_without_config_or_roots_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "CONFIG_PATH", tmp_path / "absent.json")
    with pytest.raises(bl.ConfigError, match="Run /brain-init"):
        bl.rebuild_index(tmp_path)


def test_explicit_vault_path_works_with_a_config(tmp_path, monkeypatch):
    personal = tmp_path / "personal"; shared = tmp_path / "shared"
    for d in (personal, shared):
        (d / "wiki-a").mkdir(parents=True)
    (shared / "wiki-a" / "alpha.md").write_text("---\ntitle: Alpha\nsummary: s\n---\nbody\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "personalVault": {"path": str(personal)},
        "sharedTree": {"path": str(shared)},
    }))
    monkeypatch.setattr(bl, "CONFIG_PATH", config)
    assert bl.main(["find", "alpha", str(shared)]) == 0
    assert bl.main(["rebuild", str(shared)]) == 0
    assert (shared / "index.md").exists()


def test_private_log_goes_to_the_private_root(tmp_path, monkeypatch):
    personal = tmp_path / "personal"; shared = tmp_path / "shared"; private = tmp_path / "private"
    for d in (personal, shared, private):
        d.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "personalVault": {"path": str(personal)},
        "sharedTree": {"path": str(shared)},
        "privateVault": {"path": str(private)},
    }))
    monkeypatch.setattr(bl, "CONFIG_PATH", config)

    assert bl.main(["log", "--private", "graduate", "side-project"]) == 0
    assert "side-project" in (private / "log.md").read_text()
    assert not (personal / "log.md").exists()

    assert bl.main(["log", "lint", "work"]) == 0
    assert "side-project" not in (personal / "log.md").read_text()


def test_private_log_without_private_root_falls_back_to_personal(tmp_path, monkeypatch):
    personal = tmp_path / "personal"; shared = tmp_path / "shared"
    personal.mkdir(); shared.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "personalVault": {"path": str(personal)},
        "sharedTree": {"path": str(shared)},
        "privateVault": {"declined": True},
    }))
    monkeypatch.setattr(bl, "CONFIG_PATH", config)
    assert bl.main(["log", "--private", "capture", "x"]) == 0
    assert "CAPTURE" in (personal / "log.md").read_text()
