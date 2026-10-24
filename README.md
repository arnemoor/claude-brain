# claude-brain

A Claude Code plugin marketplace with one plugin, `brain`: long-term memory for Claude Code, kept as an Obsidian vault of Markdown pages in git.

- Every session starts with the vault's must-know rules and one line on how to search it. Nothing else is injected, so the vault can grow without filling the context window.
- Skills capture sources, distil finished project journals into pages, lint and reconcile the vault, check time-sensitive claims, and write a weekly digest.
- One vault for one person (solo), or a personal, a shared team and a private repository side by side (tiered).

## Install

```
/plugin marketplace add arnemoor/claude-brain
/plugin install brain@claude-brain
/brain-init
```

See [plugins/brain/README.md](plugins/brain/README.md) for setup, config and commands.
