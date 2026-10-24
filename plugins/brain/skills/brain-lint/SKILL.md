---
name: brain-lint
description: Read-only integrity check of the Claude LLM-Wiki vault. Checks frontmatter, dead links, orphan pages, unique slugs, topic-gloss coverage, must-know size and grounding, SessionStart payload size, restated facts, incidental cross-links, and runs a hard secrets scan that fails loudly on any hit. Use for the /brain-lint slash command (add --fix to apply safe fixes) and natural-language triggers like "lint the vault", "check the wiki for problems", "scan the brain for secrets", "find stale or irrelevant links", or "vault health check".
model: sonnet
---

> **First-run check.** Before doing anything else, read `~/.config/brain/config.json`. If it is missing or incomplete, invoke the **brain-init** skill to set up the brain, then continue this task. If present, use its roots and `freshnessSources`.

# brain-lint

Integrity check of the vault at `$CLAUDE_VAULT`. Read whichever of `personalVault.path`, `sharedTree.path` and `privateVault.path` are set in the config before acting. A solo vault sets only `privateVault.path`. READ-ONLY by default. Apply changes only when invoked as `/brain-lint --fix`, and even then only safe fixes. Works from ANY cwd.

## Checks
`brain_check.py` is the single source of truth for checks 0 through 6. A run must not write its own validation script, because the committed checker makes results reproducible and diffable between runs.

Run these commands with `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" <check>` and interpret the JSON findings:

0. `validate` first, then `index`. Treat `unparseable` pages as loud hard failures because they are invisible to the index.
1. `validate` for the typed frontmatter schema: title, summary, sources, last_updated, tags, and pin.
2. `malformed` and `links` for broken bracket syntax, dead wikilinks, and dead source citations. Links to slugs listed in a root's `external-slugs.txt` come back under `external`. They point into another vault that is cloned only on some machines, so report their count only, never as dead.
3. `orphans` compares zero-inbound pages with a deliberately recorded local baseline: `new` are regressions, `resolved` were in the baseline but are no longer orphaned, and `known` remain expected. `ok` is false only when `new` is non-empty. Ask the user before recording or replacing the baseline with `orphans --set-baseline`, ordinary checks never write it.
4. `slugs` for globally duplicate slugs across all roots.
5. `glosses` for topic coverage per root, and `mustknow` for the must-know cap and line length.
6. `secrets` for literal private-key blocks and assignment-shaped opaque values. Keyword mentions and a Vault path are not findings.
7. Incidental cross-links. For each `[[link]]` in a page BODY, judge whether the target is topically relevant to the source page's core topic (its title/summary). This is a relevance judgment, so READ the page, it is not a resolvable-link check. Read the full source page because relevance depends on the carrying sentence, and do not infer relevance from titles. Check the target page's SUMMARY before opening its full page, because the summary is usually enough to judge whether the target is topically related. Flag a link as **incidental** when it is inline prose introduced by provenance phrasing ("first hit", "came in via", "same session as", "discovered during", "originally", "e.g. during <unrelated thing>") AND the target's subject is unrelated to the source (the canonical case: a docx-formatting page linking an AI-tool-approval page only because the bug first surfaced during that assessment). Classify each: CLEAR (incidental, target unrelated), BORDERLINE (weak or arguable relevance, or a `## Related`-section link that looks off), or RELEVANT. Report CLEAR and BORDERLINE with the source slug, target slug, and the carrying sentence. Do not flag genuinely on-topic links, and do not strip a curated `## Related` section wholesale. When unsure, treat as BORDERLINE. For a big sweep across many pages, fan this check out over disjoint page batches.

## By design, never report these
- In a tiered vault the parent folder is not a git repository. Only the configured personal, shared and private roots are repositories. A solo vault is its own repository.
- The vault-level `index.md` and each root's `MUSTKNOW.md` are generated, untracked, and gitignored. A fresh clone has no catalog until a rebuild.
- Nothing lists pages into a session. A page is found with `brain_lib.py find`, which groups hits under their `topics.yaml` folder gloss.
- The `glosses` check verifies every wiki folder has a gloss, which heads that folder's `find` results. The `mustknow` check verifies `must_know` stays at or under 30 lines of at most 160 characters each.
- The `grounding` check asks whether each `must_know` line is actually supported by the page carrying it. A rule injects into every session while its page does not, so the two can drift apart. It compares the distinctive words of the rule against the page body. It catches a rule asserting things the page never discusses. It does NOT catch a rule that contradicts the page in the page's own vocabulary, so a clean result is not proof the rule is right.
- The `restated` check is ADVISORY and never fails. It lists pages that use a distinctive phrase from another page's slug without linking to it, which the vault schema asks you to avoid so that one concept lives on one page. Two pages can legitimately share a phrase, so each hit needs reading. Its blind spot is the important one: it finds an unlinked mention of a TOPIC, not a page restating a FACT in its own words.
- The `payload` check runs the SessionStart hook exactly as Claude Code does and measures each command. Claude Code replaces hook output over 10000 characters with a 2KB preview (claude-code#44086), which silently drops the whole payload. This check is the only thing that catches it.
- Uncommitted state in any brain repository is normal. Solo, personal and private changes auto-commit on the next brain write, while shared changes wait for `/brain-contribute`.
- A known standing orphan item is summarised as a count, not listed again in full.

## Frontmatter write contract
Apply this contract to every creation or edit of frontmatter.
- Write `last_updated` and every other date field as an unquoted `YYYY-MM-DD` calendar date. Never write a datetime, an ISO timestamp, or a quoted date. `freshness_checked` is a mapping of checked dimension to unquoted date, such as `{ git: 2026-07-31 }`. When deriving a date from a file mtime, convert the mtime to its local calendar date first.
- Single quote a string that contains `: ` or starts with a character YAML treats specially. Double every internal single quote. This applies especially to `title` and `summary`.
- Change only the requested field. Never reformat, reorder, requote, or normalise any other key. Preserve the existing key order and the document's existing quoting style.
- Before writing, retain the original content. After writing, re-parse the file with `yaml.safe_load` and confirm it yields a mapping with the expected schema keys. If it does not, restore the original content exactly and report the page. A page that fails to parse is invisible to the index, which is worse than an unfixed page.
- Never invent a field that is not in the applicable schema, and never drop one that is.

## Body write rule
When an edit removes or rewrites a wikilink, re-scan the edited line and confirm every `[[` has a matching `]]` and that no neighbouring link lost its brackets. If the line no longer parses, restore the original line and report it. This exists because a production Related-line cleanup consumed the closing brackets of the preceding link and shipped corrupted markup.

## Output
Emit a summary with a pass or fail verdict per check plus the offending files. A secrets hit is a hard failure regardless of the other checks.

## Fix mode
With `--fix`, apply only safe and unambiguous fixes: repair a link to a renamed slug, backfill `last_updated` from the file mtime as an unquoted local `YYYY-MM-DD` date, normalize a slug, and remove a CLEAR incidental cross-link (check 7: delete the link, and the surrounding clause if it exists only to carry that incidental reference, keeping all durable content and its existing wording). Follow the [Frontmatter write contract](#frontmatter-write-contract) for every frontmatter edit and the [Body write rule](#body-write-rule) for every body link edit. NEVER delete a page, NEVER merge without asking, NEVER remove a BORDERLINE or `## Related`-section link on `--fix` (report those instead), and NEVER edit a secret away silently (report it instead so the user removes the whole page or line deliberately).

## Log
Record every run, read-only or fix:
```
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" log LINT "<pass or fail summary>"
```
In a tiered vault with `privateVault.path` set, keep non-work page and journal names out of the personal log. Log work that touched only private-root pages with `log --private`. For mixed work, log the work part normally with private pages only counted, and log the private part with `log --private`.
Run `brain_lib.py rebuild` only if `--fix` actually changed pages. Do NOT rebuild in read-only mode.

After that rebuild in `--fix` mode, run `save` on each configured root you changed, `personalVault.path` and `privateVault.path`:
```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/brain_git.sh" save "<root>" "LINT: <short summary>"
```

Each root has its own remote, so each is committed and pushed separately. A root without a remote is committed locally only.
Save and push the personal and private repositories directly. They need no pull request. Never push the shared repository directly. Shared changes go through `/brain-contribute` as a pull request.
