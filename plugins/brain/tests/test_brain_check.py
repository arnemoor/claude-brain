import pathlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace


HERE = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("brain_check", HERE / "scripts" / "brain_check.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


def page(title, summary="summary", body="", **overrides):
    values = {
        "title": title, "summary": summary, "sources": [], "last_updated": "2026-07-30",
        "tags": [], "pin": False,
    }
    values.update(overrides)
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, str):
            lines.append(f"{key}: {value!r}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n---\n" + body


def write(root, slug, text, folder="wiki"):
    path = root / folder / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def pages(*roots):
    return bc.load_pages(list(roots))


def git(repo, *args, date=None):
    env = os.environ.copy()
    if date:
        env.update({"GIT_AUTHOR_DATE": f"{date}T12:00:00 +0000", "GIT_COMMITTER_DATE": f"{date}T12:00:00 +0000"})
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, capture_output=True, env=env)


def git_page(repo, slug, updated, body="body"):
    path = repo / "wiki" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {slug}\nsummary: summary\nsources: []\nlast_updated: {updated}\ntags: []\npin: false\n---\n{body}\n"
    )
    return path


def init_git_repo(path):
    subprocess.run(["git", "init", str(path)], check=True, text=True, capture_output=True)
    git(path, "config", "user.email", "tests@example.invalid")
    git(path, "config", "user.name", "Brain tests")


def test_validate_reports_unparseable_page(tmp_path):
    root = tmp_path / "vault"
    bad = write(root, "bad", "---\ntitle: [broken\n---\nbody")
    result = bc.validate(pages(root))
    assert result["unparseable"][0]["file"] == str(bad)


def test_freshness_checked_accepts_only_a_dimension_mapping(tmp_path):
    root = tmp_path / "vault"
    write(root, "bare", page("bare").replace("---\n", "---\nfreshness_checked: 2026-07-13\n", 1))
    write(root, "new", page("new").replace("---\n", "---\nfreshness_checked: { ticket_status: 2026-07-30 }\n", 1))
    flagged = [item["file"] for item in bc.validate(pages(root))["findings"] if item["field"] == "freshness_checked"]
    assert len(flagged) == 1 and flagged[0].endswith("bare.md")


def test_freshness_checked_rejects_an_unknown_dimension(tmp_path):
    root = tmp_path / "vault"
    write(root, "odd", page("odd").replace("---\n", "---\nfreshness_checked: { weather: 2026-07-30 }\n", 1))
    fields = [item["field"] for item in bc.validate(pages(root))["findings"]]
    assert "freshness_checked" in fields


def test_claim_extractors_mask_code_and_find_the_five_dimensions(tmp_path):
    root = tmp_path / "vault"
    write(root, "claims", page("claims", body="""The Worker is deployed on Cloudflare.

As of 2026-09-18 the main branch is merged.

The sticker shop is currently offline.

Source is at github.com/example/shop and gitea.example.net/me/brain.

```sh
git branch -D old   # as of 2026-01-01 this is done, currently deployed
```

`The release is done today`
"""))
    extracted = bc.extract_claims(pages(root))
    assert [i["line"] for i in extracted["cloudflare"]] == [1]
    assert len(extracted["dated_as_of"]) == 1 and "2026-09-18" in extracted["dated_as_of"][0]["text"]
    assert len(extracted["git"]) == 1 and "main branch" in extracted["git"][0]["text"]
    assert len(extracted["current_facts"]) == 1 and "offline" in extracted["current_facts"][0]["text"]
    assert extracted["github"][0]["refs"] == ["github.com/example/shop"]
    assert extracted["gitea"][0]["refs"] == ["gitea.example.net/me/brain"]


def test_claims_reports_candidates_and_never_a_pass(tmp_path):
    root = tmp_path / "vault"
    write(root, "claims", page("claims", body="As of 2026-09-18 the deploy is live " + "x" * 160 + "."))
    write(root, "quiet", page("quiet", body="Nothing time sensitive here."))
    default = bc.claims(pages(root))
    verbose = bc.claims(pages(root), verbose=True)
    assert default["claims_found"] >= 1
    dated = default["dimensions"]["dated_as_of"]
    assert dated["claims_found"] == 1
    assert dated["status"] == "candidates found, verify each against its live source"
    assert dated["candidates"][0]["slug"] == "claims"
    assert len(dated["candidates"][0]["excerpt"]) <= 80
    assert verbose["dimensions"]["dated_as_of"]["candidates"][0]["text"].endswith("x" * 160 + ".")
    assert default["dimensions"]["gitea"]["status"] == "no verifiable claims present"
    assert set(default["dimensions"]) == set(bc.FRESHNESS_DIMENSIONS)


def test_links_listed_in_external_slugs_are_external_not_dead(tmp_path):
    root = tmp_path / "vault"
    write(root, "page", page("page", body="[[work-page]] and [[missing]]"))
    (root / "external-slugs.txt").write_text("# lives in a work vault\nwork-page\n")
    result = bc.links(pages(root), [root])
    assert [item["target"] for item in result["dead"]] == ["missing"]
    assert [item["target"] for item in result["external"]] == ["work-page"]


def test_freshness_checked_still_accepts_legacy_dimension_names(tmp_path):
    root = tmp_path / "vault"
    write(root, "old", page("old").replace("---\n", "---\nfreshness_checked: { confluence: 2026-09-18, github: 2026-09-18 }\n", 1))
    assert not [item for item in bc.validate(pages(root))["findings"] if item["field"] == "freshness_checked"]


def test_apply_claim_fix_updates_exact_claim_and_freshness_marker(tmp_path):
    fixture = HERE / "tests" / "fixtures" / "stale-status.md"
    target = tmp_path / "stale-status.md"
    target.write_text(fixture.read_text())
    original = target.read_text()
    bc.apply_claim_fix(target, "ABC-9 is Open", "ABC-9 is Resolved", ["ticket_status"], bc.dt.date(2026, 7, 31))
    updated = target.read_text()
    assert "ABC-9 is Resolved" in updated
    assert "freshness_checked: { ticket_status: 2026-07-31 }" in updated
    assert updated.replace("ABC-9 is Resolved", "ABC-9 is Open").replace(
        "freshness_checked: { ticket_status: 2026-07-31 }\n", "") == original
    assert bc.load_pages([tmp_path])[0]["frontmatter"]["title"] == "stale status"


def test_links_report_dead_wikilink_and_source(tmp_path):
    root = tmp_path / "vault"
    write(root, "page", page("page", body="[[missing]] and [[source: gone.md]]"))
    result = bc.links(pages(root), [root])
    assert {(item["kind"], item["target"]) for item in result["dead"]} == {("wikilink", "missing"), ("source", "gone.md")}


def test_links_default_omits_resolved_inventory_but_verbose_includes_it(tmp_path):
    root = tmp_path / "vault"
    write(root, "one", page("one", body="[[two]] and [[missing]]"))
    write(root, "two", page("two"))
    default = bc.links(pages(root), [root])
    verbose = bc.links(pages(root), [root], verbose=True)
    assert default["scanned"] == 2
    assert all(not item["resolved"] for item in default["links"])
    assert any(item["resolved"] for item in verbose["links"])


def test_orphans_default_omits_full_inbound_map(tmp_path):
    root = tmp_path / "vault"
    baseline = tmp_path / "baseline.json"
    write(root, "one", page("one", body="[[two]]"))
    write(root, "two", page("two"))
    result = bc.orphans(pages(root), baseline_path=baseline)
    assert result["page_count"] == 2
    assert result["inbound"] == []
    assert result["zero"] == ["one"]


def test_orphans_new_orphan_fails_against_baseline(tmp_path):
    root = tmp_path / "vault"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"zero": ["known"]}))
    write(root, "known", page("known"))
    write(root, "new", page("new"))
    result = bc.orphans(pages(root), baseline_path=baseline)
    assert not result["ok"]
    assert result["new"] == ["new"]
    assert result["known"] == ["known"]


def test_orphans_resolved_orphan_is_reported(tmp_path):
    root = tmp_path / "vault"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"zero": ["linked", "resolved"]}))
    write(root, "linked", page("linked", body="[[resolved]]"))
    write(root, "resolved", page("resolved"))
    result = bc.orphans(pages(root), baseline_path=baseline)
    assert result["ok"]
    assert result["resolved"] == ["resolved"]


def test_orphans_without_baseline_does_not_mark_existing_pages_new(tmp_path):
    root = tmp_path / "vault"
    baseline = tmp_path / "missing.json"
    write(root, "orphan", page("orphan"))
    result = bc.orphans(pages(root), baseline_path=baseline)
    assert result["ok"]
    assert result["new"] == []
    assert result["unclassified"] == ["orphan"]
    assert result["baseline"]["status"] == "absent"


def test_set_baseline_writes_current_zero_set_and_reports_count(tmp_path, monkeypatch, capsys):
    root = tmp_path / "vault"
    baseline = tmp_path / "state" / "orphans-baseline.json"
    write(root, "orphan", page("orphan"))
    monkeypatch.setattr(bc, "ORPHANS_BASELINE_PATH", baseline)
    assert bc.main(["orphans", str(root), "--set-baseline"]) == 0
    result = json.loads(capsys.readouterr().out)["orphans"]
    assert result["baseline"] == {"status": "recorded", "count": 1}
    assert result["ok"]
    assert result["known"] == ["orphan"]
    assert result["new"] == []
    assert json.loads(baseline.read_text()) == {"zero": ["orphan"]}


def test_malformed_catches_real_related_line_corruption_and_period(tmp_path):
    root = tmp_path / "vault"
    write(root, "broken", page("broken", body="Related: [[access-sync-controller]], [[hr-job-portal]], [[cpt-app-gitops-blueprint."))
    write(root, "period", page("period", body="See [[a-sentence.]]"))
    findings = bc.malformed(pages(root))["findings"]
    assert any(item["error"] == "opening without closing" and "cpt-app-gitops-blueprint." in item["text"] for item in findings)
    assert any(item["error"] == "punctuation inside link text" for item in findings)


def test_malformed_ignores_exact_inline_swift_type_signature(tmp_path):
    root = tmp_path / "vault"
    write(root, "jagdash", page("jagdash", body="- `scanForTroubleCodes() async throws -> [ECUID: [TroubleCode]]`"))
    assert bc.malformed(pages(root))["findings"] == []


def test_malformed_still_catches_links_outside_code_and_ignores_fenced_code(tmp_path):
    root = tmp_path / "vault"
    write(root, "code", page("code", body="```swift\nlet type = [ECUID: [TroubleCode]]\n```\nBroken [[outside"))
    findings = bc.malformed(pages(root))["findings"]
    assert len(findings) == 1 and findings[0]["text"] == "[[outside"


def test_slugs_reports_duplicates_across_roots(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    write(one, "same", page("same"))
    write(two, "same", page("same"))
    assert bc.slugs(pages(one, two))["duplicates"][0]["slug"] == "same"


def test_glosses_flags_a_folder_with_no_gloss(tmp_path):
    root = tmp_path / "vault"
    (root / "wiki-a").mkdir(parents=True)
    (root / "wiki-b").mkdir(parents=True)
    (root / "topics.yaml").write_text('wiki-a: "glossed"\n')
    pages = [
        {"root": str(root), "relative_path": "wiki-a/alpha.md", "slug": "alpha",
         "frontmatter": {}},
        {"root": str(root), "relative_path": "wiki-b/bravo.md", "slug": "bravo",
         "frontmatter": {}},
        # a flat file at the root is not a page and contributes no folder
        {"root": str(root), "relative_path": "README.md", "slug": "README",
         "frontmatter": {}},
    ]
    result = bc.glosses(pages, [root])
    assert result["unglossed"] == [{"root": "vault", "folder": "wiki-b"}]
    assert result["ok"] is False


def test_glosses_pass_when_every_folder_is_glossed(tmp_path):
    root = tmp_path / "vault"
    (root / "wiki-a").mkdir(parents=True)
    (root / "topics.yaml").write_text('wiki-a: "glossed"\n')
    pages = [{"root": str(root), "relative_path": "wiki-a/alpha.md", "slug": "alpha",
              "frontmatter": {}}]
    assert bc.glosses(pages, [root])["ok"] is True


def test_mustknow_flags_an_oversized_rule(tmp_path):
    root = tmp_path / "vault"
    pages = [
        {"root": str(root), "relative_path": "wiki-a/alpha.md", "slug": "alpha",
         "frontmatter": {"must_know": "short rule"}},
        {"root": str(root), "relative_path": "wiki-b/bravo.md", "slug": "bravo",
         "frontmatter": {"must_know": ["x" * 200]}},
    ]
    result = bc.mustknow(pages, [root])
    assert result["mustknow_lines"] == 2
    assert [r["slug"] for r in result["over_length"]] == ["bravo"]
    assert result["ok"] is False


def test_mustknow_passes_within_the_caps(tmp_path):
    root = tmp_path / "vault"
    pages = [{"root": str(root), "relative_path": "wiki-a/alpha.md", "slug": "alpha",
              "frontmatter": {"must_know": "short rule"}}]
    assert bc.mustknow(pages, [root])["ok"] is True


def test_payload_reports_each_hook_command_under_the_limit(tmp_path):
    result = bc.payload([tmp_path])
    assert result["hard_limit"] == 10000
    # one command now, so nothing can be squeezed out by an index that grows
    assert [m["mode"] for m in result["modes"]] == ["mustKnow"]
    for mode in result["modes"]:
        assert mode["chars"] < result["hard_limit"], mode
        assert not mode["truncated_by_claude_code"]

def test_secrets_ignores_vault_path_but_finds_private_key(tmp_path):
    root = tmp_path / "vault"
    write(root, "path", page("path", body="The token lives at Vault path `kv/team/token`."))
    write(root, "key", page("key", body="-----BEGIN PRIVATE KEY-----\nopaque"))
    findings = bc.secrets(pages(root))["findings"]
    assert len(findings) == 1 and findings[0]["kind"] == "private key block"


def test_index_reconciliation_catches_mismatch(tmp_path):
    root = tmp_path / "vault"
    write(root, "one", page("one"))
    (root / "index.md").write_text("# Wiki Index\n")
    assert not bc.index_check(pages(root), [root])["ok"]


def test_candidates_include_summaries_and_ignore_split_chapter_family(tmp_path):
    root = tmp_path / "vault"
    write(root, "guide-01", page("one", "first chapter"))
    write(root, "guide-02", page("two", "second chapter"))
    result = bc.candidates(pages(root))
    assert result["pairs"] == []
    assert result["total_pairs_considered"] == 1


def test_candidates_filter_unrelated_emit_similar_score_and_cap(tmp_path):
    root = tmp_path / "vault"
    write(root, "jag-obd", page("Jag OBD diagnostics command line tool", "Jaguar OBD diagnostic command line tool"))
    write(root, "jag-obd-cli", page("Jag OBD diagnostics command line utility", "Jaguar OBD diagnostics command line utility"))
    write(root, "hr-job", page("HR job portal", "Applicant assessment workflow"))
    result = bc.candidates(pages(root), limit=1)
    assert result["threshold"] == 20 and result["cap"] == 1
    assert result["qualifying_pairs"] == 1 and result["dropped_by_cap"] == 0
    assert result["pairs"][0]["score"] >= result["threshold"]
    assert {item["slug"] for item in result["pairs"][0]["pair"]} == {"jag-obd", "jag-obd-cli"}


def test_candidates_reports_pairs_dropped_by_cap(tmp_path):
    root = tmp_path / "vault"
    for slug in ("api-alpha", "api-beta", "api-gamma"):
        write(root, slug, page("API integration command tool", "API integration command tool"))
    result = bc.candidates(pages(root), limit=1)
    assert result["qualifying_pairs"] == 3
    assert len(result["pairs"]) == 1 and result["dropped_by_cap"] == 2


def test_all_omits_candidate_detail_without_claiming_pairs_were_dropped(tmp_path, monkeypatch, capsys):
    root = tmp_path / "vault"
    baseline = tmp_path / "baseline.json"
    for slug in ("api-alpha", "api-beta", "api-gamma"):
        write(root, slug, page("API integration command tool", "API integration command tool"))
    monkeypatch.setattr(bc, "ORPHANS_BASELINE_PATH", baseline)
    assert bc.main(["all", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)["candidates"]
    assert result["qualifying_pairs"] == 3
    assert result["detail_omitted_for_size"] is True
    assert result["detail_subcommand"] == "candidates"
    assert "dropped_by_cap" not in result
    assert "cap" not in result


def test_changed_uses_last_updated_when_first_commit_is_after_window(tmp_path):
    repo = tmp_path / "recreated"
    init_git_repo(repo)
    git_page(repo, "migrated", "2026-07-30")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "migration", date="2026-07-29")
    result = bc.changed([repo], bc.dt.date(2026, 7, 27))["repositories"][0]
    assert result["history_truncated"] is True
    assert result["source_used"] == "last_updated"
    assert result["edited"] == ["wiki/migrated.md"]
    assert result["cross_check"]["status"] == "skipped"
    assert "mismatches" not in result["cross_check"]


def test_changed_uses_git_and_classifies_new_and_edited_pages(tmp_path):
    repo = tmp_path / "history"
    init_git_repo(repo)
    old = git_page(repo, "old", "2026-07-20")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial", date="2026-07-20")
    git_page(repo, "new", "2026-07-30")
    old.write_text(old.read_text() + "updated body\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "weekly work", date="2026-07-30")
    result = bc.changed([repo], bc.dt.date(2026, 7, 27))["repositories"][0]
    assert result["history_truncated"] is False
    assert result["source_used"] == "git"
    assert result["new"] == ["wiki/new.md"]
    assert result["edited"] == ["wiki/old.md"]


def test_changed_reports_git_change_without_last_updated_bump(tmp_path):
    repo = tmp_path / "mismatch"
    init_git_repo(repo)
    page_path = git_page(repo, "stale-date", "2026-07-20")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial", date="2026-07-20")
    page_path.write_text(page_path.read_text() + "body changed without frontmatter update\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "bad hygiene", date="2026-07-30")
    result = bc.changed([repo], bc.dt.date(2026, 7, 27))["repositories"][0]
    assert result["cross_check"] == {"status": "ran", "mismatches": [{"relative_path": "wiki/stale-date.md", "last_updated": "2026-07-20"}]}


def test_changed_default_output_omits_full_git_and_last_updated_inventories(tmp_path, capsys):
    repo = tmp_path / "default"
    init_git_repo(repo)
    git_page(repo, "page", "2026-07-30")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "migration", date="2026-07-29")
    assert bc.main(["changed", str(repo), "--since", "2026-07-27"]) == 0
    result = json.loads(capsys.readouterr().out)["changed"]["repositories"][0]
    assert "git_changes" not in result
    assert "last_updated_changes" not in result


def _pg(slug, body="", must_know=None, folder="wiki-a"):
    return {"root": "/v", "file": f"/v/{folder}/{slug}.md",
            "relative_path": f"{folder}/{slug}.md", "slug": slug,
            "body": body, "frontmatter": {"must_know": must_know} if must_know else {}}


def test_grounding_passes_when_the_rule_terms_are_on_the_page():
    page = _pg("flux-escaping",
               "FluxCD substitutes every ${VAR} in a rendered manifest, so escape it.",
               must_know="FluxCD substitutes every ${VAR} in a rendered manifest.")
    result = bc.grounding([page])
    assert result["ok"] is True and result["findings"] == []


def test_grounding_flags_a_rule_the_page_does_not_support():
    # the page is about podman, the rule is about tunnels. exactly the drift that shipped.
    page = _pg("podman-local", "Notes on running podman compose stacks on this machine.",
               must_know="Confirm which host runs a tunnel before reaching for local lsof or curl.")
    result = bc.grounding([page])
    assert result["ok"] is False
    finding = result["findings"][0]
    assert finding["slug"] == "podman-local"
    for term in ("tunnel", "lsof", "curl"):
        assert term in finding["ungrounded"]


def test_grounding_checks_every_entry_of_a_list():
    page = _pg("styles", "Write short sentences.",
               must_know=["Write short sentences.",
                          "Never publish a quarterly forecast without treasury approval."])
    result = bc.grounding([page])
    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule"].startswith("Never publish")


def test_grounding_ignores_pages_with_no_must_know():
    assert bc.grounding([_pg("plain", "body text")])["ok"] is True


def test_restated_flags_an_unlinked_topic_mention():
    pages = [_pg("email-domains", "The domains Acme sends mail from."),
             _pg("git-author", "Pick the right email domains for the repository, "
                               "since email domains differ by department.")]
    result = bc.restated(pages)
    hit = [f for f in result["findings"] if f["owner"] == "email-domains"]
    assert hit and "email domains" in hit[0]["phrases"]


def test_restated_is_satisfied_by_a_wikilink():
    pages = [_pg("email-domains", "The domains Acme sends mail from."),
             _pg("git-author", "See [[email-domains]] for which email domains exist.")]
    assert [f for f in bc.restated(pages)["findings"] if f["owner"] == "email-domains"] == []


def test_restated_ignores_a_phrase_that_is_shared_vocabulary():
    # a phrase several pages use is vocabulary, not a topic one page owns
    pages = [_pg("cloudflare-zero-trust", "Zero trust access.")]
    pages += [_pg(f"user-{i}", "We enabled cloudflare zero trust last year.") for i in range(4)]
    assert bc.restated(pages)["findings"] == []


def test_restated_still_sees_a_two_word_slug():
    # a two-word slug yields exactly one phrase, so rarity has to carry it alone
    pages = [_pg("email-domains", "The domains Acme sends mail from."),
             _pg("git-author", "Pick the right email domains for the repository.")]
    hits = [f for f in bc.restated(pages)["findings"] if f["owner"] == "email-domains"]
    assert hits and hits[0]["phrases"] == ["email domains"]


def test_restated_is_advisory_not_a_failure():
    pages = [_pg("email-domains", "The domains Acme sends mail from."),
             _pg("git-author", "Which email domains apply, and how email domains map to teams.")]
    result = bc.restated(pages)
    assert result["findings"] and result["ok"] is None


def test_without_config_check_fails_instead_of_passing_on_zero_pages(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bc.brain_lib, "CONFIG_PATH", tmp_path / "absent.json")
    assert bc.main(["validate"]) == 1
    assert "Run /brain-init" in capsys.readouterr().err
