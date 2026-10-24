---
name: brain-sync
description: Reconcile the Claude LLM-Wiki vault. Validate every page's frontmatter, resolve and fix dead [[wikilinks]] and [[source:]] citations, and merge overlapping pages non-destructively. Use for the /brain-sync slash command and natural-language triggers like "sync the vault", "reconcile my wiki", "fix broken links in the brain", or "clean up the vault".
model: sonnet
---

> **First-run check.** Before doing anything else, read `~/.config/brain/config.json`. If it is missing or incomplete, invoke the **brain-init** skill to set up the brain, then continue this task. If present, use its roots and `freshnessSources`.

# brain-sync

Reconcile the vault at `$CLAUDE_VAULT`. Read whichever of `personalVault.path`, `sharedTree.path` and `privateVault.path` are set in the config before acting (a solo vault sets only `privateVault.path`), then read each root's `CLAUDE.md` for its own tree rules. Works from ANY cwd. This operation may edit pages, so make surgical changes and never delete anything without asking first.

`brain_check.py` is the single source of truth for mechanical checks. A run must not write its own validation script, because the committed checker makes results reproducible and diffable between runs.

## 0. Pull shared knowledge and rebuild catalogs
Before reconciling anything, read `personalVault.path`, `sharedTree.path` and `privateVault.path` from `~/.config/brain/config.json`. Pull each one that is set, then rebuild with `$CLAUDE_VAULT` set to the vault: the solo vault itself, or the PARENT of the repositories in a tiered vault:
```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/brain_git.sh" pull "<sharedTree.path>"     # only when sharedTree.path is set
bash "${CLAUDE_PLUGIN_ROOT}/scripts/brain_git.sh" pull "<privateVault.path>"   # only when privateVault.path is set
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" rebuild "$CLAUDE_VAULT"
```
A pull of a root without a remote is skipped. A good pull also resets the hourly session-start sync. The rebuild reads the configured roots in personal, shared, then private order. The generated `index.md` belongs at that vault. Each root keeps its own generated `MUSTKNOW.md`, plus a hand-maintained `topics.yaml` that is source, not generated. The glosses in `topics.yaml` head a folder's results in `brain_lib.py find`.

## 1. Validate frontmatter
Run `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" validate` and `index`, then interpret the JSON. Report unparseable pages first and loudly. Before every frontmatter write, follow the [Frontmatter write contract](../brain-lint/SKILL.md#frontmatter-write-contract). Backfill an obvious gap, for example a missing `last_updated` from the file mtime as an unquoted local `YYYY-MM-DD` date. Ask before inventing a summary or a title.

## 2. Resolve links
Run `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" malformed` and `links`, then interpret the JSON. List every malformed or dead link together with its host page. Fix a dead link only when the intended target is unambiguous, for example a renamed slug or a moved source file. Otherwise leave it and report it for the user to resolve. Never touch a link under `external`. Its slug is listed in `external-slugs.txt` because it lives in another vault. For every body link edit, follow the [Body write rule](../brain-lint/SKILL.md#body-write-rule).

## 3. Reconcile duplicates
Use this cheap-to-expensive ladder:

1. Consume `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" candidates`. Its title, summary, slug similarity, and inbound signals produce CANDIDATES only. They never justify a merge proposal.
2. Read both candidates' SUMMARIES supplied by that output. This is the primary discriminator and settles most cases. Reject a candidate when a summary describes itself as an index or a set, because it is navigation rather than duplicate content. Also reject pages whose summaries name different LAYERS of one system, such as a configuration script and a backend implementation, or different ARTIFACT types in one pipeline, such as text metadata and screenshots. Respect its mechanical split-chapter disqualifier for a shared slug prefix with a numbered or language suffix.
3. Only when the summaries genuinely do NOT distinguish the pages, read both bodies in full. This is the expensive step and the exception, not the default.
4. A page being merely THIN is never a merge reason. A short page can be a complete atomic fact or an index.

This ladder is ordered this way because in the observed failed proposals the summaries already held the distinguishing information and were skipped. It forces use of that signal rather than adding reading.

A real merge case is the same fact stated twice with no distinguishing scope, or a stub that adds nothing its sibling does not already say. For example, a 30-word page beside a 2000-word page of the same name can be a merge candidate after the summaries and, when needed, both bodies have been reviewed.

For every proposed merge, state the survivor, what would be LOST by merging, and how the merged page preserves every sourced fact and both citation sets. Propose only. Never delete or overwrite a page without explicit confirmation from the user. When an approved merge is applied, redirect inbound `[[slug]]` links to the surviving page. Report every rejected candidate and its reason so a later run does not re-propose it.

## 4. Reindex and log
After changes, run:
```
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" rebuild
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" log SYNC "<what was reconciled>"
```
In a tiered vault with `privateVault.path` set, keep non-work page and journal names out of the personal log. Log work that touched only private-root pages with `log --private`. For mixed work, log the work part normally with private pages only counted, and log the private part with `log --private`.
Then report a short summary: pages validated, links fixed, duplicates merged or flagged, and anything still needing the user's decision.

After the final rebuild, run `save` on each configured root you changed, `personalVault.path` and `privateVault.path`:
```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/brain_git.sh" save "<root>" "SYNC: <short summary>"
```

Each root has its own remote, so each is committed and pushed separately. A root without a remote is committed locally only.
Save and push the personal and private repositories directly. They need no pull request. Never push the shared repository directly. Shared changes go through `/brain-contribute` as a pull request.
