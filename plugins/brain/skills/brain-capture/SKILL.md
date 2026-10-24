---
name: brain-capture
description: Capture a URL, local file, or repo doc into the Claude LLM-Wiki vault as an immutable source, then optionally synthesize a wiki page. Use for the /brain-capture slash command and natural-language triggers like "capture this into the wiki", "save this to my brain", "ingest this page", "add this to the vault", or "remember this source".
model: sonnet
---

> **First-run check.** Before doing anything else, read `~/.config/brain/config.json`. If it is missing or incomplete, invoke the **brain-init** skill to set up the brain, then continue this task. If present, use its roots and `freshnessSources`.

# brain-capture

Ingest external knowledge into the vault at `$CLAUDE_VAULT`. Read whichever of `personalVault.path`, `sharedTree.path` and `privateVault.path` are set in the config before acting. A solo vault sets only `privateVault.path`. The configured roots together are the vault, and each root's `CLAUDE.md` defines its own allowed trees. Works from ANY cwd, never from the current project.

## 1. Fetch the raw material
Resolve what the user handed you and pull the full text.
- URL: run `uv run --with html2text python "${CLAUDE_PLUGIN_ROOT}/scripts/brain_fetch.py" "<url>"`. WebFetch returns a model summary and would make the immutable source a paraphrase.
- Local file: Read it.
- Repo doc: GitHub via `gh`, Gitea via `tea`, any other host via `git`.

## 2. Write an immutable source
Before creating source or page frontmatter, follow the [Frontmatter write contract](../brain-lint/SKILL.md#frontmatter-write-contract).
For a URL, write the `markdown` field from `brain_fetch.py` output exactly, after checking `char_count` and any `warning`. Do not write a source from a warned extraction without telling the user.
Write the raw capture to `<root>/sources/<YYYY-MM-DD>-<slug>.md`. The root is `personalVault.path` for work knowledge, and `privateVault.path` for non-work knowledge or in a solo vault, so non-work material never reaches a work remote. The slug is lowercase, hyphenated, type-prefix stripped, and globally unique across all roots. NEVER edit an existing source file. If the target name already exists, add a numeric suffix and write a new file.
Source frontmatter:
```
---
source_url: <url, path, or repo ref>
ingested_at: <ISO8601-UTC>
content_type: <url | file | repo>
title: <human title>
---
```
The body is the fetched content verbatim. Do NOT rewrite or summarize it here.

## 3. Synthesize or update a wiki page (optional)
If the capture warrants a durable note, create or update exactly ONE wiki page in the correct configured root and tree. Read every root's `CLAUDE.md` file first, then use their schema and classification rules.
Use the page frontmatter schema: title, summary, sources (list), last_updated (YYYY-MM-DD), tags (list, for example [work] or [me, projects]), pin (bool).

Two optional fields shape what reaches a session:

- `must_know:` is a string or a list of one-line imperatives injected verbatim into every session. The admission test is narrow: add one only when a session that does not apply it unprompted produces wrong output. One fact means one line, and a corollary belongs in the body. Keep each line at or under 160 characters. The cap across all roots is 30 lines.
- The `summary:` is NOT injected. It appears in `index.md` and when someone reads the page, so write it for a reader, not for a budget.

If the page lands in a wiki folder that does not exist yet, add a gloss for that folder to the root's `topics.yaml`. `brain-lint` fails on an unglossed folder, because the gloss is how a session decides whether to open anything in it.

Quote any frontmatter value that contains a colon followed by a space, or a backslash. An unquoted one breaks the YAML, and a page with broken frontmatter is skipped silently by every rebuild.
Cite the source inline with `[[source: <YYYY-MM-DD>-<slug>.md]]` and cross-link related concepts with `[[slug]]`. Prefer a link over repeating a fact.

## 4. Sensitivity gate
Place durable team knowledge in the shared root, private work knowledge in the personal root, and non-work knowledge such as side projects and personal life in the private root when `privateVault.path` is set, according to those schemas. Keep secrets, credentials, tokens, passwords, and private keys OUT of the vault entirely. If the material contains a secret, capture the knowledge and redact the secret.

## 5. Reindex and log
```
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" rebuild
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/brain_lib.py" log CAPTURE "<one line on what was captured>"
```
In a tiered vault with `privateVault.path` set, keep non-work page and journal names out of the personal log. Log work that touched only private-root pages with `log --private`. For mixed work, log the work part normally with private pages only counted, and log the private part with `log --private`.

## 6. Save
After the rebuild, run `save` on each configured root you changed, `personalVault.path` and `privateVault.path`:
```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/brain_git.sh" save "<root>" "CAPTURE: <short summary>"
```

Each root has its own remote, so each is committed and pushed separately. A root without a remote is committed locally only.
Save and push the personal and private repositories directly. They need no pull request. Never push the shared repository directly. Shared changes go through `/brain-contribute` as a pull request.
