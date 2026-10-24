# brain

An LLM-Wiki memory system for Claude Code. Knowledge lives in Markdown Git repositories that the assistant maintains, shaped as an Obsidian vault.
The must-know rules and a search pointer are injected at every session start, so knowledge compounds across sessions and directories instead of being re-explained.

## Install

```
/plugin marketplace add arnemoor/claude-brain
/plugin install brain@claude-brain
/brain-init
```

The machine needs `uv` and `git`. `gh` and `tea` are optional and only matter for `/brain-freshness` and `/brain-contribute`.

## Two layouts

- **Solo**: one vault in one Git repository. The remote is optional. Without one, every save is a local commit, so back the folder up, for example with Time Machine. This is the right choice for one person.
- **Tiered**: a personal work repository and a shared team repository side by side under one parent, plus an optional private repository for side projects and personal life on a remote of your own. The parent is the Obsidian vault root, so wikilinks resolve across all repositories.

## First run

No manual setup is needed. Any brain command reads `~/.config/brain/config.json` (or the file named by `$BRAIN_CONFIG`) and invokes `/brain-init` when it is missing.
brain-init checks for `uv` and Git, asks solo or tiered, asks for the vault paths and remotes, sets each repository's Git identity, wires the machine, rebuilds catalogs, and reports its changes.

A solo config looks like this:

```json
{
  "version": 1,
  "privateVault": {"path": "/Users/me/brain", "remote": null},
  "journalsDir": "/Users/me/private/projects",
  "freshnessSources": {"git": true, "gh": false, "gitea": false, "cloudflare": false},
  "wiring": {"launchdWeekly": false}
}
```

A tiered config adds `personalVault` and `sharedTree` (with `"prWorkflow": true`) next to `privateVault`.

## Sync at session start

When a root's last sync is more than an hour old, the SessionStart hook syncs it before it reads the rules: it fetches, rebases onto origin, pushes local commits, and rebuilds `MUSTKNOW.md` and `index.md` when HEAD moved. It covers every root except the shared tree, which only takes pull requests. It leaves a root alone on uncommitted changes, a rebase in progress, or a branch other than the remote's default branch. It aborts a rebase that stops, and it retries after 10 minutes when the remote is unreachable. The session sees one line only when something changed or needs attention.

- `BRAIN_AUTOSYNC=0` turns it off.
- `BRAIN_SYNC_INTERVAL` and `BRAIN_SYNC_RETRY` set the two intervals in seconds, default 3600 and 600.

Give a vault `log.md merge=union` in `.gitattributes` when two machines write it. Both prepend to `log.md`, and union keeps both entries instead of stopping the rebase.

## Commands

| Command | Purpose |
| --- | --- |
| `/brain-init` | Bootstrap or reconfigure the brain, solo or tiered. |
| `/brain-capture` | Capture a source and optionally synthesize a wiki page. |
| `/brain-digest` | Summarize recent vault changes and write a dated digest. |
| `/brain-freshness` | Verify time-sensitive claims against git, GitHub, Gitea and Cloudflare. |
| `/brain-graduate` | Distil project journals into durable vault knowledge. |
| `/brain-lint` | Check frontmatter, links, structure, and secrets. |
| `/brain-sync` | Reconcile pages, links, and duplicates without destructive changes. |
| `/brain-contribute` | Prepare shared changes as a pull request (tiered only). |

Solo, personal and private knowledge auto-commits after every write and pushes when the repository has a remote. Shared knowledge goes through `/brain-contribute` as a pull request, never a direct push. Pull and reindex weekly or on demand with `/brain-sync`.

## Staying current with the team

This section applies only to a tiered vault with a shared tree. Once a day, at the first session start, the plugin fast-forwards the shared repository's default branch from origin. You receive what the team merged without running any command, which matters if you mostly read the brain rather than write to it.

If you sit on your `/brain-contribute` branch, that branch is never touched. The default branch is advanced behind it without a checkout, and the session offers to rebase your branch onto it. The rebase runs only if you say yes, and it stops and reports conflicts rather than resolving or pushing anything.

The hook never rewrites history, never blocks a session, and stays silent unless there is something for you to decide. The check is skipped when the remote is unreachable, so being offline costs nothing and simply retries at the next session. The day is marked as checked only after a fetch actually succeeds.

## Nudges

The plugin also helps you move your own work into the vault. It shows at most one nudge per session start, never in the middle of a task, and never on a compaction. Claude asks before it does anything. It acts only on an explicit yes. On a no, or when you ignore the question, it drops the subject for the rest of the session.

| Nudge | When it appears | What Claude offers |
| --- | --- | --- |
| Weekly agent | You opted in to the weekly maintenance agent and its wrapper still predates the nudges. It shows at most once a day and comes before the other nudges. | `/brain-init`, which refreshes the wrapper and plist. |
| Pending journal | The last session wrote a progress journal that has content the brain does not have yet. | To ask whether the project is finished. On a yes it offers `/brain-graduate <slug>`. |
| Finished journal | You said a project is finished and its journal was never graduated. | `/brain-graduate <slug>`. |
| Stale journal | An open journal has had no new entry for 14 days. | The same question about the project. |
| Contribute | Tiered only. The shared repository has changes that are not in a pull request yet. New pages that Git does not track yet count too. | `/brain-contribute`. |
| Backlog | Journals from before the nudges existed were never graduated. | To graduate them all in one batch, with one review, and one pull request when a shared tree is configured. |
| Dry feed | There is no journal 21 days after the first run. | To check the journaling rules in your `~/.claude/CLAUDE.md`. |

While you work, a `Stop` check asks Claude to update the session's journal when real work happened since the last journal write. It fires after 3 edits or remote writes, or 1 commit or push, since the last journal write. It stays silent for smaller sessions and in throwaway worktrees. It needs `journalsDir` in `~/.config/brain/config.json`, which `/brain-init` writes. At the end of a session, a `SessionEnd` hook only notes which journals you touched, so the next session can ask about them.

A no is never permanent, because finished work belongs in the vault. A backlog dig that ends before it finishes is offered again at the next session start, outside the Friday cadence. A session that starts while a dig is still running gets no such offer. A declined backlog offer comes back on the next Friday at 13:00, or at the first session start after that. Say no on a Friday and it comes back the Friday after. Stale journals come back after 14 days. A project you called not finished is not asked about again after each session, only through the 14 day rule.

The weekly maintenance job runs headless sessions. They set `BRAIN_NUDGE_OFF=1`, so they show no nudge and leave the nudge state alone. An install that already has the weekly agent gets this setting when you run `/brain-init` again. The agent keeps its old wrapper until then, so the start hook offers `/brain-init` once a day while the old wrapper is in place.

Nudge state lives in `~/.config/brain/nudge-state.json`. It holds local data only, such as when you were last asked, and never leaves your machine. Delete the file to start over. Nudges read local files and local Git refs and never touch the network. The nudges never write a journal name to a vault log, a commit message or a pull request. During graduation in a tiered vault, the names of non-work journals stay out of them too.

## What it changes on your machine

The plugin updates `settings.json` environment and `autoMemoryEnabled`, adds a marked block to your user `CLAUDE.md`, writes `~/.config/brain` (including `last-master-check`, the daily stamp described above, and `nudge-state.json`, the nudge state described above), registers `Stop` and `SessionEnd` hooks next to the `SessionStart` hooks for the nudges, and can install an optional weekly launchd agent `com.github.arnemoor.brain.vault-maintenance` that is off unless you opt in. To remove the agent, run `launchctl unload ~/Library/LaunchAgents/com.github.arnemoor.brain.vault-maintenance.plist` and delete that plist.

`index.md` and `MUSTKNOW.md` are generated locally and deliberately not committed. A fresh clone has no catalog until brain-init rebuilds it.

A session is given the must-know rules and one line telling it the vault is searchable. Nothing else is injected, so a session pays no token cost for a page list. Run `brain_lib.py find <query>` to search every page by slug, title, summary, tag and folder topic. It also matches common word forms, so `verification` finds `verify`. `pin: true` ranks a page first among equal matches, and `index.md` holds the full catalog.

## Hermes

`hermes/` is a Hermes plugin that gives a Hermes profile the same vault. It adds one system prompt section per session: the find pointer, the plugin root path, the sync note and every root's `MUSTKNOW.md`. Hermes expands only `${HERMES_*}` tokens in a skill, so the section tells the agent which path `${CLAUDE_PLUGIN_ROOT}` stands for. To wire a profile, link the plugin, enable it and add the skills:

```
ln -s <checkout>/plugins/brain/hermes ~/.hermes/plugins/brain
# ~/.hermes/config.yaml
plugins:
  enabled: [brain]
skills:
  external_dirs: [<checkout>/plugins/brain/skills]
```

It reads the same `~/.config/brain/config.json`, so run `/brain-init` in Claude Code first.

## Tests

```
cd plugins/brain
uv run --with pytest --with pyyaml --with html2text --with requests pytest -q tests
for t in tests/*.sh; do bash "$t"; done
```
