#!/usr/bin/env python3
"""Idempotent machine writers for brain-init. Each function is safe to run twice."""
import json, os, shlex, shutil, subprocess, sys
from pathlib import Path

CONFIG_PATH   = Path(os.environ.get("BRAIN_CONFIG") or Path.home()/".config"/"brain"/"config.json").expanduser()
SETTINGS_PATH = Path.home()/".claude"/"settings.json"
CLAUDE_MD_PATH= Path.home()/".claude"/"CLAUDE.md"
TEMPLATE_DIR  = Path(__file__).parent.parent/"templates"
FEED_TEMPLATE = TEMPLATE_DIR/"feed-rules.md"
LAUNCHD_TEMPLATE = TEMPLATE_DIR/"launchd-vault-maintenance.plist.tmpl"
CLAUDE_BIN    = shutil.which("claude") or str(Path.home()/".local"/"bin"/"claude")
DEFAULT_LAUNCHD_LABEL = "com.github.arnemoor.brain.vault-maintenance"
MARKETPLACE, PLUGIN = "claude-brain", "brain"
FEED_START, FEED_END = "<!-- brain-feed:start -->", "<!-- brain-feed:end -->"
FEED_SIGNS    = ("brain_lib.py find", "/brain-capture", "/brain-graduate")


def expand(p): return Path(os.path.expandvars(os.path.expanduser(str(p))))


def _load_json(p, default):
    return json.loads(Path(p).read_text()) if Path(p).exists() else dict(default)


def write_config(cfg, path=CONFIG_PATH):
    path = expand(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n")


def wire_settings(vault_path, settings=SETTINGS_PATH):
    # Registers no hook: the plugin's hooks.json supplies the SessionStart hook.
    s = _load_json(settings, {}); changed = {}
    env = s.setdefault("env", {})
    if env.get("CLAUDE_VAULT") != str(vault_path):
        env["CLAUDE_VAULT"] = str(vault_path); changed["CLAUDE_VAULT"] = str(vault_path)
    if s.get("autoMemoryEnabled") is not False:
        s["autoMemoryEnabled"] = False; changed["autoMemoryEnabled"] = False
    Path(settings).write_text(json.dumps(s, indent=2) + "\n")
    return changed


def install_feed_rules(journals_dir, append_anyway=False, claude_md=CLAUDE_MD_PATH, template=FEED_TEMPLATE):
    """Write the feed rules between the brain-feed markers of CLAUDE.md.

    Returns True when the file changed and False when it already matched. A file
    without markers that already carries the template's headings or the brain
    commands gets no silent second copy: nothing is written and the call returns
    {"existing": [those lines]} so brain-init can ask. append_anyway=True
    appends once the user has agreed."""
    text = Path(template).read_text().replace("{JOURNALS}", str(journals_dir)).rstrip()
    block = FEED_START + "\n" + text + "\n" + FEED_END
    cur = Path(claude_md).read_text() if Path(claude_md).exists() else ""
    if FEED_START in cur:
        import re
        new = re.sub(re.escape(FEED_START)+r".*?"+re.escape(FEED_END), block, cur, flags=re.S)
    else:
        if not append_anyway:
            heads = {l.strip().lower() for l in text.splitlines() if l.startswith("## ")}
            found = [f"{n}: {l.strip()[:120]}" for n, l in enumerate(cur.splitlines(), 1)
                     if l.strip().lower() in heads or any(s in l for s in FEED_SIGNS)]
            if found:
                return {"existing": found}
        new = (cur.rstrip() + "\n\n" if cur.strip() else "") + block + "\n"
    if new == cur:
        return False
    Path(claude_md).write_text(new); return True


def scaffold_vault(path, trees, template):
    v = expand(path); v.mkdir(parents=True, exist_ok=True)
    if not (v/".git").exists():
        subprocess.run(["git","init","-q",str(v)], check=True)
    # Write the schema only into a new vault. On a re-run the file may carry the
    # user's own tree layout, and brain-init promises a safe re-run, so an
    # existing schema is left for brain-init to reconcile with the user.
    if not (v/"CLAUDE.md").exists():
        (v/"CLAUDE.md").write_text(Path(template).read_text())
    (v/"log.md").touch(exist_ok=True)
    gitignore = v / ".gitignore"
    ignored = gitignore.read_text().splitlines() if gitignore.exists() else []
    # index.md and MUSTKNOW.md are written by `brain_lib.py rebuild`, so a clone
    # regenerates them and never tracks them.
    missing = [f for f in ("index.md", "MUSTKNOW.md") if f not in ignored]
    if missing:
        gitignore.write_text("\n".join([*ignored, *missing]).lstrip("\n") + "\n")
    for t in trees:
        d = v/t; d.mkdir(parents=True, exist_ok=True); (d/".gitkeep").touch(exist_ok=True)


def install_launchd(template=LAUNCHD_TEMPLATE, label=DEFAULT_LAUNCHD_LABEL,
                    vault_path=None, log_dir=None):
    if vault_path is None:
        raise ValueError("vault_path is required to install the launchd agent")

    dest = Path.home()/"Library"/"LaunchAgents"/f"{label}.plist"
    vault = expand(vault_path)
    logs = expand(log_dir) if log_dir is not None else CONFIG_PATH.parent
    logs.mkdir(parents=True, exist_ok=True)
    wrapper = logs/"brain-maintenance"
    claude_bin = shutil.which("claude") or CLAUDE_BIN
    wrapper.write_text(
        "#!/bin/zsh\n"
        "# The plist provides an explicit PATH, so this wrapper does not need a login shell.\n"
        "config=\"${BRAIN_CONFIG:-${HOME}/.config/brain/config.json}\"\n"
        "[[ -f \"$config\" ]] || exit 0\n"
        "# Line 1 is the vault (the common parent of all roots), line 2 the shared tree or empty.\n"
        "paths=(\"${(@f)$(python3 - \"$config\" <<'PY'\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "try:\n"
        "    config = json.load(open(sys.argv[1]))\n"
        "    roots = [os.path.expanduser(config[k][\"path\"]) for k in (\"personalVault\", \"sharedTree\", \"privateVault\")\n"
        "             if (config.get(k) or {}).get(\"path\")]\n"
        "    shared = (config.get(\"sharedTree\") or {}).get(\"path\") or \"\"\n"
        "except (OSError, ValueError, KeyError, TypeError, AttributeError):\n"
        "    sys.exit(0)\n"
        "if roots:\n"
        "    print(os.path.commonpath(roots) if len(roots) > 1 else roots[0])\n"
        "    print(os.path.expanduser(shared))\n"
        "PY\n"
        ")}\")\n"
        "[[ -n \"${paths[1]}\" ]] || exit 0\n"
        "vault=\"${paths[1]}\"\n"
        "shared=\"${paths[2]}\"\n"
        "cd \"$vault\" || exit 1\n"
        "export CLAUDE_VAULT=\"$vault\"\n"
        "# Headless runs must not use up the interactive nudges.\n"
        "export BRAIN_NUDGE_OFF=1\n"
        f"plugin_root=\"${{HOME}}/.claude/plugins/marketplaces/{MARKETPLACE}/plugins/{PLUGIN}\"\n"
        "if [[ ! -f \"$plugin_root/scripts/brain_git.sh\" || ! -f \"$plugin_root/scripts/brain_lib.py\" ]]; then\n"
        f"  plugin_root=\"$(python3 - \"${{HOME}}/.claude/plugins/cache/{MARKETPLACE}/{PLUGIN}\" <<'PY'\n"
        "from pathlib import Path\n"
        "import re\n"
        "import sys\n"
        "cache = Path(sys.argv[1])\n"
        "candidates = [path for path in cache.iterdir() if path.is_dir() and (path / 'scripts' / 'brain_git.sh').is_file() and (path / 'scripts' / 'brain_lib.py').is_file()] if cache.is_dir() else []\n"
        "def version(path):\n"
        "    return tuple(int(value) for value in re.findall(r'\\d+', path.name))\n"
        "if candidates:\n"
        "    print(max(candidates, key=version))\n"
        "PY\n"
        ")\"\n"
        "fi\n"
        "if [[ -z \"$plugin_root\" || ! -f \"$plugin_root/scripts/brain_git.sh\" || ! -f \"$plugin_root/scripts/brain_lib.py\" ]]; then\n"
        "  print -u2 -- \"brain maintenance: plugin scripts are unavailable\"\n"
        "  exit 1\n"
        "fi\n"
        f"claude_bin={shlex.quote(claude_bin)}\n"
        "if [[ ! -x \"$claude_bin\" ]]; then\n"
        "  claude_bin=\"$(command -v claude 2>/dev/null)\"\n"
        "fi\n"
        "if [[ -z \"$claude_bin\" || ! -x \"$claude_bin\" ]]; then\n"
        "  print -u2 -- \"brain maintenance: claude executable is unavailable\"\n"
        "  exit 1\n"
        "fi\n"
        "if [[ -n \"$shared\" ]]; then\n"
        "  bash \"$plugin_root/scripts/brain_git.sh\" pull \"$shared\" || exit 1\n"
        "fi\n"
        "uv run \"$plugin_root/scripts/brain_lib.py\" rebuild || exit 1\n"
        f"\"$claude_bin\" -p {shlex.quote('/brain-lint --fix')} && "
        f"\"$claude_bin\" -p {shlex.quote('/brain-digest')}\n"
    )
    wrapper.chmod(0o755)
    plist = Path(template).read_text()
    for placeholder, value in {
        "{VAULT}": str(vault),
        "{LOG_DIR}": str(logs),
        "{LABEL}": label,
        "{HOME}": str(Path.home()),
        "{WRAPPER}": str(wrapper),
    }.items():
        plist = plist.replace(placeholder, value)

    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl","unload",str(dest)], capture_output=True)
    dest.write_text(plist)
    subprocess.run(["launchctl","load",str(dest)], check=True)
    return {"dest": dest, "wrapper": wrapper}


if __name__ == "__main__":
    fn, args = sys.argv[1], sys.argv[2:]

    def parse_arg(arg):
        try:
            return json.loads(arg)
        except (json.JSONDecodeError, TypeError):
            return arg

    out = globals()[fn](*[parse_arg(arg) for arg in args])
    if out is not None:
        print(out if isinstance(out, (str, Path)) else json.dumps(out, default=str))
