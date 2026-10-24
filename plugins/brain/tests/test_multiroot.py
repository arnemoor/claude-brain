import importlib.util, json, os, pathlib, subprocess
here = pathlib.Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("brain_lib", here / "scripts" / "brain_lib.py")
bl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bl)

PAGE = "---\ntitle: {t}\nsummary: {s}\ntags: [{tag}]\npin: {pin}\n---\nbody\n"
PAGE_MK = "---\ntitle: {t}\nsummary: {s}\ntags: [{tag}]\npin: {pin}\nmust_know: {mk}\n---\nbody\n"

def _mk(root, tree, slug, pin="false", must_know=None):
    d = root / tree; d.mkdir(parents=True, exist_ok=True)
    tpl = PAGE if must_know is None else PAGE_MK
    (d / f"{slug}.md").write_text(tpl.format(t=slug, s=f"sum-{slug}", tag="x", pin=pin, mk=must_know))

def test_rebuild_spans_two_roots(tmp_path):
    personal = tmp_path/"personal"; shared = tmp_path/"shared"
    _mk(personal, "wiki-me", "alpha", pin="true")
    _mk(shared, "wiki-team", "beta")
    n = bl.rebuild_index(personal, roots=[personal, shared])
    idx = (personal/"index.md").read_text()
    assert n == 2 and "[[alpha]]" in idx and "[[beta]]" in idx
    assert "2 pages across wiki-me, wiki-team." in idx

def test_rebuild_index_skips_non_wiki_top_level_trees(tmp_path):
    vault = tmp_path/"vault"
    _mk(vault, "wiki-me", "alpha", pin="true")
    # A source capture with a YAML-ish header (source_url/ingested_at, no title/summary)
    # parses as a non-empty dict just like a real page, so it must be excluded by tree,
    # not by frontmatter shape. Regression check for the sources/ leak into index.md.
    sources = vault/"sources"; sources.mkdir(parents=True, exist_ok=True)
    (sources/"2026-01-01-some-capture.md").write_text(
        "---\nsource_url: https://example.com\ningested_at: 2026-01-01T00:00:00\n---\nraw capture\n"
    )
    n = bl.rebuild_index(vault, roots=[vault])
    idx = (vault/"index.md").read_text()
    assert n == 1 and "[[alpha]]" in idx
    assert "some-capture" not in idx and "## sources" not in idx

def test_rebuild_mustknow_collects_string_and_list_rules(tmp_path):
    personal = tmp_path/"personal"; shared = tmp_path/"shared"
    _mk(personal, "wiki-me", "one", must_know='"single rule"')
    _mk(shared, "wiki-team", "two", must_know="\n  - \"first\"\n  - \"second\"")
    _mk(shared, "wiki-team", "plain")

    assert bl.rebuild_mustknow(personal, roots=[personal, shared]) == 3
    assert (personal/"MUSTKNOW.md").read_text().count("- single rule [[one]]") == 1
    shared_mk = (shared/"MUSTKNOW.md").read_text()
    assert "- first [[two]]" in shared_mk and "- second [[two]]" in shared_mk
    assert "plain" not in shared_mk


def test_rebuild_mustknow_removes_file_when_no_rules_remain(tmp_path):
    vault = tmp_path/"vault"
    _mk(vault, "wiki-me", "one", must_know='"a rule"')
    assert bl.rebuild_mustknow(vault, roots=[vault]) == 1
    assert (vault/"MUSTKNOW.md").exists()

    (vault/"wiki-me"/"one.md").write_text(PAGE.format(t="one", s="s", tag="x", pin="false"))
    assert bl.rebuild_mustknow(vault, roots=[vault]) == 0
    assert not (vault/"MUSTKNOW.md").exists()
