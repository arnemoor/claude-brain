---
name: brain-graduate
description: Distil a matured progress journal from the configured journalsDir (default ${HOME}/private/projects) into durable vault knowledge - pull the decisions, gotchas, reusable facts, architecture and how-tos out of a project's results log into the right wiki-* pages, non-destructively and incrementally, leaving the journal in place as history. Use for the /brain-graduate slash command (/brain-graduate <slug>, /brain-graduate --scan or /brain-graduate --backlog) and natural-language triggers like "graduate this project", "distil the project into the wiki", "promote the keepers to the vault", "this project is done, capture what matters", "dig the journal backlog", "graduate all old journals".
model: opus
---

> **First-run check.** Before doing anything else, read `~/.config/brain/config.json`. If it is missing or incomplete, invoke the **brain-init** skill to set up the brain, then continue this task. If present, use its roots and `freshnessSources`.

# brain-graduate
Vault root: `$CLAUDE_VAULT`. Read every configured root first (a solo vault has only `privateVault`), then read each root's `CLAUDE.md` for its tree rules. Project journals: `<journalsDir>/<slug>.md` (the working RESULTS logs, per ~/.claude/CLAUDE.md "Progress journaling"). `journalsDir` is the key in `~/.config/brain/config.json`, default `${HOME}/private/projects` when absent. This skill moves the DURABLE knowledge out of a matured journal into the vault, leaving the journal itself untouched as the project history.

## Invocation
- `/brain-graduate <slug>` - distil `<journalsDir>/<slug>.md`. After the user answers "finished" to a session-start question, no further call is needed. The start hook sees the new marker.
- `/brain-graduate --scan` - list journals in `journalsDir` that have real progress content but no (or a stale) graduation marker, and propose which to distil.
- `/brain-graduate --backlog` - dig all never-graduated journals in one batch with one review, see Backlog mode.

## Steps
1. Read `<journalsDir>/<slug>.md`. Look for a graduation marker comment at the top:
   `<!-- brain-graduate: graduated_at=<ISO> graduated_to=[[a]],[[b]] -->`. If present, only consider content added AFTER that timestamp (journals are dated append logs); otherwise consider the whole file.
2. Separate DURABLE KNOWLEDGE from PROGRESS NOISE:
   - GRADUATE: decisions and their rationale ("why we chose X"), gotchas and pitfalls, reusable facts, architecture, infra details, non-obvious constraints, how-tos.
   - LEAVE in the journal: task-by-task status, changelogs, done-checklists, run results, milestone dates. If there is no durable knowledge, say so, write the marker with an empty `graduated_to=` as in step 5, and stop.
3. Classify each knowledge item into the correct configured root and tree by reading that root's `CLAUDE.md`. CREATE a new page or MERGE into the existing page on that topic. Before creating or editing its frontmatter, follow the [Frontmatter write contract](../brain-lint/SKILL.md#frontmatter-write-contract). Give each page the standard frontmatter (title, summary, sources, last_updated, tags, pin). Add a `must_know:` line only when a session that does not apply the rule unprompted produces wrong output, one fact per line, at most 160 characters. Quote any value containing a colon followed by a space or a backslash, or the page is silently skipped. When you set `freshness_checked`, use only these dimensions as keys: `dated_as_of`, `current_facts`, `git`, `github`, `gitea`, `cloudflare`. Any other key fails validation. If the page needs a new wiki folder, add that folder's gloss to the root's `topics.yaml`. Add provenance in the body: the issue reference if the slug or body has one, plus "distilled from project `<slug>`".
4. NON-DESTRUCTIVE, confirm first (like brain-sync): show the proposed page creates/edits and get an OK before writing. Never edit or delete journal content beyond adding/updating the marker in step 5.
5. Update the marker at the top of the journal to the current time and append the pages you wrote:
   `<!-- brain-graduate: graduated_at=<ISO now> graduated_to=[[page-a]],[[page-b]] -->`. This keeps re-runs incremental and idempotent. When every proposal was struck, write the same marker with `graduated_to=` empty. The journal then leaves the finished and stale nudges until it gets a new entry.
6. Rebuild + log via the shared lib:
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" rebuild`
   then `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" log GRADUATE "<slug> -> <n> pages"`.
   In a tiered vault with `privateVault.path` set, keep non-work page and journal names out of the personal log. Log work that touched only private-root pages with `log --private`. For mixed work, log the work part normally with private pages only counted, and log the private part with `log --private`.
7. Report what graduated and what was left as noise. Write page prose in the vault style: short active sentences, no em-dash, en-dash or semicolon. Keep secrets and credentials out of the vault.
8. After the rebuild, validate the frontmatter you wrote:
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" validate`
   Stop on `"ok": false`. Fix the pages and fields it names, then run it again until it reports `"ok": true`. Save only after that.
   Then run `save` on each configured root you changed, `personalVault.path` and `privateVault.path`:
   ```
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/brain_git.sh" save "<root>" "GRADUATE: <short summary>"
   ```

   Each root has its own remote, so each is committed and pushed separately. A root without a remote is committed locally only.
   Save and push the personal and private repositories directly. They need no pull request. Never push the shared repository directly. Shared changes go through `/brain-contribute` as a pull request.

## Backlog mode
`/brain-graduate --backlog` digs every journal the nudge state lists as never graduated. It reuses the steps above with one review for the whole batch. A single yes at the start covers the batch, so skip the per-page confirmation of step 4 and use the review below instead.
1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain_nudge.py" backlog start` at the start of every run, a resumed one too. Then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain_nudge.py" backlog list` to get the slugs still to dig. Journals whose marker covers their newest entry are skipped and not listed. A stopped run is offered again at the next session start, outside the Friday cadence, and continues with the rest.
2. Rank the slugs. Judge each journal from its slug, its H1 title and its Primer lines only, the first 15 lines. Never read whole journals in the main session. Work journals first, then most dated entries, then most recent.
3. Dispatch Sonnet subagents at high effort, one group of journals each, in parallel. A subagent reads its journals in full, classifies each as work or private itself, and applies steps 1 to 3. It writes nothing and returns proposed page creates and edits with source journals and a one-line gist. Extend an existing page before creating one.
4. Merge the proposals per page, so two journals that feed the same page become one edit. Show one review per configured root (shared, personal, private) with page, create or extend, source journals and gist. List journals with nothing durable. The user approves once or strikes lines.
5. Write the approved pages. Right after a journal's pages are written, set its marker as in step 5. A journal with nothing durable, or one whose proposals were all struck, gets `graduated_to=` empty, so it leaves the backlog.
6. Rebuild and log as in step 6 with one batch line, `log GRADUATE "backlog -> <n> pages"`, and `--private` for the private part. Then run `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" validate`. Stop on `"ok": false`, fix the named fields and run it again. Only then save the personal and private roots as in step 8.
7. When a shared tree is configured, run `/brain-contribute` for the shared pages as one pull request.

In a solo vault every journal graduates into its one root. In a tiered vault, non-work journals graduate into the private root only. When `privateVault.path` is not configured there, a non-work journal gets a marker with empty `graduated_to=` and the run writes no pages for it. The per-page merge never combines a private journal's item into a shared or personal page. Their slugs stay in the local review. Keep them out of every log entry, commit message and pull request.
