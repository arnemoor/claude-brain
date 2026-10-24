---
name: brain-freshness
description: Verify time-sensitive claims in the Claude LLM-Wiki vault against their live sources (git, GitHub via gh, Gitea via tea, Cloudflare), flag each FRESH, STALE, or UNVERIFIABLE, auto-fix clear-cut staleness, and surface the judgment calls. Use for the /brain-freshness slash command and natural-language triggers like "freshness check the vault", "is the wiki stale", or "verify the brain against the repos".
model: opus
---

> **First-run check.** Before doing anything else, read `~/.config/brain/config.json`. If it is missing or incomplete, invoke the **brain-init** skill to set up the brain, then continue this task. If present, use its roots and `freshnessSources`.

# brain-freshness

Guard against documenting outdated knowledge. Vault pages are distilled from sources that keep moving (issues close, releases ship, deployments move), so a claim that was true when written drifts. This check re-verifies the time-sensitive claims against their live sources and repairs the clear drift.

Vault root: `$CLAUDE_VAULT`. Read every configured root first (a solo vault has only `privateVault`), then read each root's `CLAUDE.md` when classifying pages. Works from ANY cwd. Read-only against the sources, write only to the vault.

## 1. Select scope
Default (no arg): every page whose body carries a **time-sensitive claim** (a status like "in progress" / "done" / "planned" / "blocked", an ownership or assignee claim, a dated "as of <date>", a migration-progress statement, a "current" fact) that has no `freshness_checked` marker or whose marker is older than 30 days.

Args narrow it: `--pinned` (pages with `pin: true` plus every page carrying a `must_know:` line), `--folder <path>` (a path inside one configured root), `--page <slug>`, `--all`, `--stale-first` (order by oldest `freshness_checked`). Pure provenance (a bare issue reference with no claim about its state) is NOT a time-sensitive claim, skip it.

## 2. Extract the verifiable claims
For each target page, list the specific claims worth checking and the backing reference for each:
- First run `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_check.py" claims`. It reports every dimension as `claims_found` plus compact candidates. Nothing in it is verified, so a non-empty result is a to-do list and never a pass. Read the explicit status of each dimension.
- **git**: a local repo, branch, tag, file, or "exists" claim. Verify read-only with `git`.
- **GitHub**: a repo, release, issue, or PR claim. Verify read-only with `gh`, for example `gh repo view`, `gh release list`, `gh issue view`.
- **Gitea**: a repo, release, issue, or PR claim on a Gitea host. Verify read-only with `tea`, for example `tea repos`, `tea releases ls`, `tea issues`. If `tea` is not logged in, mark the claim UNVERIFIABLE.
- **Cloudflare**: a Worker, Pages project, D1 database, R2 bucket, or DNS claim. Verify read-only through the Cloudflare MCP or API, never with a write call.
Ignore untestable prose (rationale, how-tos, durable facts with no moving source).

The mechanical signal produces candidates. Only reading the carrying paragraph resolves them. Skip these recurring false-positive classes on sight unless the surrounding prose makes a real claim:
- Status words used as ordinary verbs or adjectives, such as "open the file" or "a live preview".
- Documentation placeholders inside fenced or inline code.
- Granularity mismatches: a dated and hedged page status such as "Status 2026-07-22, release is parked, remaining gates" is not contradicted merely because a tracker says Open.

## 3. Verify and classify
For each claim, compare the page against the live source and label it:
- **FRESH**: the source still supports the claim.
- **STALE**: the source contradicts it. Record the delta (was X, now Y) with the evidence (issue state, release tag, commit date).
- **UNVERIFIABLE**: source gone, access denied, or ambiguous. Record why.
Prioritize the highest-impact claims first: pages carrying a `must_know:` line, because that text injects verbatim into every session, then pinned pages, then status claims on repos and deployments.

## 4. Apply
Before editing `freshness_checked`, `last_updated`, or any other frontmatter, follow the [Frontmatter write contract](../brain-lint/SKILL.md#frontmatter-write-contract).
- **Auto-fix clear-cut STALE** (the source unambiguously contradicts the page): correct the claim on the page. Keep issue-reference provenance, never write secrets, write in the vault style (short active sentences, no em-dash, en-dash or semicolon), and bump `last_updated` to today.
- **Surface judgment calls**: anything ambiguous or where the "right" content is a decision, list for the human. Do not guess.
- On every page you verified (fixed or confirmed fresh), set `freshness_checked` to a mapping that names only the dimensions checked, for example `freshness_checked: { git: 2026-07-31, github: 2026-07-31 }`. The allowed keys are `dated_as_of`, `current_facts`, `git`, `github`, `gitea` and `cloudflare`. Follow the frontmatter write contract and do not alter unrelated fields.

## 5. Report
Write `<root>/digests/freshness-<YYYY-MM-DD>.md` in the first configured root (`personalVault.path`, else `privateVault.path`): per page, the FRESH / STALE / UNVERIFIABLE verdicts, what you auto-fixed, and the open judgment calls with their evidence. In a tiered vault with `privateVault.path` set, report private-root pages in `<privateVault.path>/digests/freshness-<YYYY-MM-DD>.md` instead.

## 6. Reindex and log
```
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" rebuild
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" log FRESHNESS "<date>: N pages, F fresh / S stale-fixed / U unverifiable"
```
In a tiered vault with `privateVault.path` set, keep non-work page and journal names out of the personal log. Log work that touched only private-root pages with `log --private`. For mixed work, log the work part normally with private pages only counted, and log the private part with `log --private`.
Run `brain_check.py payload` and confirm every hook command reports `ok`.

## Scale and recurrence
For a large scope (dozens of pages, many repos), fan out the read-only verification across page-batches (one agent per batch runs the git, gh, tea and Cloudflare lookups and returns structured verdicts), then apply the fixes centrally so writes stay ordered. The `freshness_checked` markers make this incremental: a re-run targets never-checked and oldest-checked pages first. Run after any big consolidation, and periodically on `--pinned`.
