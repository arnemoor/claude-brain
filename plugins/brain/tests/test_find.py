import importlib.util, pathlib
here = pathlib.Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("brain_lib", here / "scripts" / "brain_lib.py")
bl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bl)

PAGE = "---\ntitle: {t}\nsummary: {s}\ntags: [{tag}]\npin: {pin}\n---\n{body}\n"


def _mk(root, tree, slug, title=None, summary="", tag="x", pin="false", body="body"):
    d = root / tree
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        PAGE.format(t=title or slug, s=summary, tag=tag, pin=pin, body=body))


def _slugs(matches):
    return [m["slug"] for m in matches]


def test_finds_a_page_by_slug_substring_ignoring_case(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "ambient-mesh-migration")
    _mk(v, "wiki-me", "unrelated-page")

    assert _slugs(bl.find_pages(v, "AMBIENT", roots=[v])) == ["ambient-mesh-migration"]


def test_slug_match_outranks_summary_match(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "zebra-page", summary="all about istio")
    _mk(v, "wiki-me", "istio-page", summary="nothing relevant")

    # alphabetically zebra-page is last, so only ranking can put istio-page first
    assert _slugs(bl.find_pages(v, "istio", roots=[v])) == ["istio-page", "zebra-page"]


def test_pin_breaks_ties_inside_one_rank_band(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "aaa-istio", pin="false")
    _mk(v, "wiki-me", "zzz-istio", pin="true")

    # both match on slug, so the pinned one wins despite sorting last
    assert _slugs(bl.find_pages(v, "istio", roots=[v])) == ["zzz-istio", "aaa-istio"]


def test_every_term_must_match(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "istio-sidecar-notes")
    _mk(v, "wiki-me", "istio-only")

    assert _slugs(bl.find_pages(v, "istio sidecar", roots=[v])) == ["istio-sidecar-notes"]


def test_terms_scattered_across_fields_match_but_rank_last(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "istio-notes", summary="covers sidecar mode")
    _mk(v, "wiki-me", "istio-sidecar", summary="nothing")

    # istio-sidecar has both terms in the slug, istio-notes has them split
    assert _slugs(bl.find_pages(v, "istio sidecar", roots=[v])) == ["istio-sidecar", "istio-notes"]


def test_body_is_not_searched_by_default(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "some-page", body="this mentions kubernetes deeply")

    assert bl.find_pages(v, "kubernetes", roots=[v]) == []
    assert _slugs(bl.find_pages(v, "kubernetes", roots=[v], search_body=True)) == ["some-page"]


def test_groups_results_under_the_folder_gloss(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me/k8s", "istio-page", summary="about istio")
    (v / "topics.yaml").write_text('wiki-me/k8s: "Kubernetes and mesh"\n')

    out = bl.format_find(bl.find_pages(v, "istio", roots=[v]), "istio")

    assert "wiki-me/k8s — Kubernetes and mesh" in out
    assert "istio-page" in out
    assert "about istio" in out


def test_folder_without_a_gloss_still_renders(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me/k8s", "istio-page")

    out = bl.format_find(bl.find_pages(v, "istio", roots=[v]), "istio")

    assert "wiki-me/k8s" in out and "—" not in out.split("\n")[2]


def test_pinned_page_is_marked(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "istio-pinned", pin="true")
    _mk(v, "wiki-me", "istio-plain", pin="false")

    out = bl.format_find(bl.find_pages(v, "istio", roots=[v]), "istio")

    assert "* istio-pinned" in out
    assert "* istio-plain" not in out


def test_no_match_suggests_the_body_flag(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "some-page")

    out = bl.format_find(bl.find_pages(v, "nothinghere", roots=[v]), "nothinghere")

    assert "No match" in out and "--body" in out


def test_limit_caps_the_number_of_pages(tmp_path):
    v = tmp_path / "v"
    for i in range(5):
        _mk(v, "wiki-me", f"istio-{i}")

    assert len(bl.find_pages(v, "istio", roots=[v], limit=3)) == 3


def test_cli_find_works_without_a_rebuilt_index(tmp_path, monkeypatch):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "istio-page", summary="about istio")
    config = tmp_path / "config.json"
    config.write_text('{"privateVault": {"path": "%s"}}' % v)
    monkeypatch.setattr(bl, "CONFIG_PATH", config)
    assert not (v / "index.md").exists()

    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = bl.main(["find", "istio", str(v)])

    assert rc == 0 and "istio-page" in buf.getvalue()


def test_stem_strips_one_suffix_and_keeps_short_words_whole():
    assert bl._stem("verification") == "verif"
    assert bl._stem("rules") == "rule"
    assert bl._stem("pods") == "pods"


def test_a_word_form_finds_its_stem(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "verify-checks-and-edge-cases")
    _mk(v, "wiki-me", "unrelated-page")

    assert _slugs(bl.find_pages(v, "verification", roots=[v])) == ["verify-checks-and-edge-cases"]


def test_an_exact_match_outranks_a_word_form_match(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me", "aaa-verify-notes")
    _mk(v, "wiki-me", "zzz-page", summary="the verification steps")

    # the slug holds only a word form, the summary holds the exact word
    assert _slugs(bl.find_pages(v, "verification", roots=[v])) == ["zzz-page", "aaa-verify-notes"]


def test_a_topic_word_finds_the_folders_pages(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me/k8s", "sidecar-notes")
    _mk(v, "wiki-me/other", "unrelated-page")
    (v / "topics.yaml").write_text('wiki-me/k8s: "Kubernetes and mesh"\n')

    assert _slugs(bl.find_pages(v, "mesh", roots=[v])) == ["sidecar-notes"]


def test_a_page_hit_outranks_a_topic_hit(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me/k8s", "aaa-notes")
    _mk(v, "wiki-me/k8s", "zzz-notes", summary="service mesh modes")
    (v / "topics.yaml").write_text('wiki-me/k8s: "Kubernetes and mesh"\n')

    # alphabetically aaa-notes is first, so only ranking can put zzz-notes first
    assert _slugs(bl.find_pages(v, "mesh", roots=[v])) == ["zzz-notes", "aaa-notes"]


def test_a_word_form_on_the_page_outranks_an_exact_topic_hit(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me/k8s", "aaa-page")
    _mk(v, "wiki-me", "zzz-verify-notes")
    (v / "topics.yaml").write_text('wiki-me/k8s: "Kubernetes verification"\n')

    # evidence on the page itself beats a word that only its folder's gloss holds
    assert _slugs(bl.find_pages(v, "verification", roots=[v])) == ["zzz-verify-notes", "aaa-page"]


def test_a_topic_word_can_supply_one_of_the_terms(tmp_path):
    v = tmp_path / "v"
    _mk(v, "wiki-me/k8s", "pdb-notes")
    _mk(v, "wiki-me/other", "pdb-elsewhere")
    (v / "topics.yaml").write_text('wiki-me/k8s: "Kubernetes and mesh"\n')

    # only the k8s page has both terms, mesh through its folder's gloss
    assert _slugs(bl.find_pages(v, "mesh pdb", roots=[v])) == ["pdb-notes"]
