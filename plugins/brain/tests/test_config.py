import json, importlib.util, pathlib, pytest
spec = importlib.util.spec_from_file_location("brain_lib", pathlib.Path(__file__).parent.parent / "scripts" / "brain_lib.py")
bl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bl)

def _cfg(tmp): 
    return {"version":1,
            "personalVault":{"path":str(tmp/"personal")},
            "sharedTree":{"path":str(tmp/"shared")},
            "journalsDir":str(tmp/"journals"),
            "freshnessSources":{"gh":True,"git":True}}

def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "CONFIG_PATH", tmp_path/"nope.json")
    assert bl.load_config() is None

def test_load_and_roots(tmp_path, monkeypatch):
    p = tmp_path/"c.json"; p.write_text(json.dumps(_cfg(tmp_path)))
    monkeypatch.setattr(bl, "CONFIG_PATH", p)
    cfg = bl.load_config()
    assert bl.config_complete(cfg) is True
    roots = bl.config_roots(cfg)
    assert [r.name for r in roots] == ["personal","shared"]

def test_invalid_json_raises(tmp_path, monkeypatch):
    p = tmp_path/"c.json"; p.write_text("{not json")
    monkeypatch.setattr(bl, "CONFIG_PATH", p)
    with pytest.raises(bl.ConfigError):
        bl.load_config()

def test_incomplete_is_false(tmp_path, monkeypatch):
    p = tmp_path/"c.json"; p.write_text(json.dumps({"version":1,"personalVault":{"path":"x"}}))
    monkeypatch.setattr(bl, "CONFIG_PATH", p)
    assert bl.config_complete(bl.load_config()) is False

def test_private_vault_is_an_optional_third_root(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path); cfg["privateVault"] = {"path": str(tmp_path/"private")}
    p = tmp_path/"c.json"; p.write_text(json.dumps(cfg))
    monkeypatch.setattr(bl, "CONFIG_PATH", p)
    cfg = bl.load_config()
    assert bl.config_complete(cfg) is True
    assert [r.name for r in bl.config_roots(cfg)] == ["personal", "shared", "private"]
