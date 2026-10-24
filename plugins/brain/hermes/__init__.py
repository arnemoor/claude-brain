"""Hermes plugin. The Hermes counterpart of hooks/session-start.sh.

It adds one system prompt section per session: the find pointer, the path the
brain skills call ${CLAUDE_PLUGIN_ROOT}, and MUSTKNOW.md from every configured
root. Like the hook, it first runs brain_autosync.sh on each root but the shared tree, which is throttled to one sync an
hour and prints a line only when something needs attention. Hermes
expands only ${HERMES_*} tokens in a SKILL.md, so the section names the path
instead. Install by linking this directory to ~/.hermes/plugins/brain,
so the resolved path below lands in the plugin checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("BRAIN_CONFIG", "~/.config/brain/config.json")).expanduser()
MAX_CHARS = 4000  # the most Hermes accepts for one section
NOTE = "[clipped to fit the section limit, see index.md]"


def _roots() -> list[tuple[str, Path]]:
    """(config key, path) for each configured root, or $CLAUDE_VAULT as a solo root."""
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    roots = [(k, Path(p).expanduser()) for k in ("personalVault", "sharedTree", "privateVault")
             if isinstance(cfg, dict) and (p := (cfg.get(k) or {}).get("path"))]
    if not roots and os.environ.get("CLAUDE_VAULT"):
        roots = [("privateVault", Path(os.environ["CLAUDE_VAULT"]).expanduser())]
    return roots


def _section(_session_info) -> str:
    lib = PLUGIN_ROOT / "scripts" / "brain_lib.py"
    parts = [
        f"Vault memory: run `uv run {lib} find <query>` to search all pages by slug, "
        "title, summary, tag and topic. Add --body for page text. Read the page before "
        "you answer from it.",
        f"The brain-* skills write `${{CLAUDE_PLUGIN_ROOT}}`. In Hermes that is `{PLUGIN_ROOT}`.",
    ]
    for key, root in _roots():
        if not root.is_dir():
            continue
        # The shared tree takes changes only through pull requests, so it is never pushed.
        if key != "sharedTree":
            try:
                note = subprocess.run(
                    ["bash", str(PLUGIN_ROOT / "scripts" / "brain_autosync.sh"), str(root)],
                    capture_output=True, text=True, timeout=20,
                ).stdout.strip().split("\n")[0]
            except (OSError, subprocess.TimeoutExpired):
                note = ""
            if note:
                parts.append(note)
        try:
            parts.append((root / "MUSTKNOW.md").read_text().strip())
        except OSError:
            pass
    text = "\n\n".join(parts)
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - len(NOTE) - 1].rsplit("\n", 1)[0] + "\n" + NOTE
    return text


def register(ctx):
    ctx.register_system_prompt_section("brain.memory", _section, position="after_memory", max_chars=MAX_CHARS)
