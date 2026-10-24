---
name: brain-digest
description: Weekly synthesis of the Claude LLM-Wiki vault. Diff since the last digest, list new and edited pages, re-pin hot pages, demote stale pages, and write a dated digest note. Use for the /brain-digest slash command and natural-language triggers like "run the weekly digest", "summarize what changed in the vault", or "digest the brain".
model: sonnet
---

> **First-run check.** Before doing anything else, read `~/.config/brain/config.json`. If it is missing or incomplete, invoke the **brain-init** skill to set up the brain, then continue this task. If present, use its roots and `freshnessSources`.

# brain-digest

Weekly synthesis for the vault at `$CLAUDE_VAULT`. Read every configured root first (a solo vault has only `privateVault`). Each root's `CLAUDE.md` is authoritative for its trees. Works from ANY cwd.

## 1. Diff since the last digest
Find the most recent digest in `<root>/digests/` of the first configured root (`personalVault.path`, else `privateVault.path`), then run:
```
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" changed --since <previous-digest-YYYY-MM-DD>
```
Interpret its JSON per configured root. Do not choose a diff source yourself. The checker decides whether git history covers the window, falls back to `last_updated` when it does not, and reports `source_used` and `history_truncated` for every repository.

## 2. List the changes
Group the changes into new pages and edited pages, split by configured root and its actual tree. Note any page that gained or lost citations and any merges since the last digest.

## 3. Re-pin and demote
Before editing `pin:` or any other frontmatter, follow the [Frontmatter write contract](../brain-lint/SKILL.md#frontmatter-write-contract).
Adjust the `pin:` frontmatter to keep search ranking current.
- Re-pin hot pages: recently edited, heavily backlinked, or referenced this week. Set pin to true.
- Demote stale pages: last_updated older than 120 days and not referenced. Set pin to false.
`pin:` only orders search results. A pinned page sorts first within its rank band in `brain_lib.py find`. There is no cap.

Maintain `topics.yaml` in each root. A new wiki folder needs a gloss, and a gloss should name what someone would search for rather than describe the folder. `brain-lint` fails on an unglossed folder.

Review `must_know:` lines. Promote a rule only when a session that does not apply it unprompted produces wrong output, and keep one fact to one line. Demote anything that is merely useful. The cap is 30 lines across all roots.

## 4. Detect shared follow-up
After re-pinning and demoting, inspect the shared root for uncommitted and unpushed changes. List affected wiki pages from `git status --porcelain`, staged and unstaged diffs, and, when an upstream exists, `git diff --name-only @{u}..HEAD`. Do not change shared git state.

If any shared pages changed, prepare this prominent follow-up for the end of the run: `Shared pages changed: <pages>. Run /brain-contribute to review and batch them.` The weekly unattended run cannot ask for this interactively, so this notice is required even when the shared changes were made by re-pinning.

## 5. Write the digest
Write `<root>/digests/digest-<YYYY-MM-DD>.md` in that same root: a recap of new and edited pages, notable merges, and the re-pin or demote decisions you made. In a tiered vault with `privateVault.path` set, keep private-root pages out of that file and write their recap to `<privateVault.path>/digests/digest-<YYYY-MM-DD>.md` instead, so no private page name reaches a work remote. The previous digest for the diff window is the latest one in either folder. If shared follow-up is needed, include a `## Shared follow-up` section with the affected pages and the instruction to run `/brain-contribute`.

## 6. Reindex and log
```
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" rebuild
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" log DIGEST "<digest date and headline counts, shared follow-up: /brain-contribute for <pages> or none>"
```
In a tiered vault with `privateVault.path` set, keep non-work page and journal names out of the personal log. Log work that touched only private-root pages with `log --private`. For mixed work, log the work part normally with private pages only counted, and log the private part with `log --private`.
Run `brain_check.py glosses`, `brain_check.py mustknow` and `brain_check.py payload` after the rebuild. All three must report `ok`. A failing `payload` means a hook command exceeded its budget and its content would not reach a session at all.

## 7. Save personal knowledge
After the final rebuild, run `save` on each configured root you changed, `personalVault.path` and `privateVault.path`:
```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/brain_git.sh" save "<root>" "DIGEST: <short summary>"
```

Each root has its own remote, so each is committed and pushed separately. A root without a remote is committed locally only.
Save and push the personal and private repositories directly. They need no pull request. Never push the shared repository directly. Shared changes go through `/brain-contribute` as a pull request.

## 8. Report completion
End the run by prominently reporting any shared follow-up and its affected pages: `Shared pages changed: <pages>. Run /brain-contribute to review and batch them.` Do not contribute or push shared automatically.
