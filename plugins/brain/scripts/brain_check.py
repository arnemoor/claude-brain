#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Reproducible, read-only integrity checks for brain vaults."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("brain_lib", HERE / "brain_lib.py")
brain_lib = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(brain_lib)

EXCLUDED_DIRS = {"sources", "digests", "attachments"}
EXCLUDED_FILES = {"CLAUDE.md", "README.md", "index.md", "MUSTKNOW.md", "log.md"}
REQUIRED = {"title": str, "summary": str, "sources": list, "tags": list,
            "pin": bool}
# Dimensions a freshness_checked mapping may name. They match the freshness
# sources brain-init offers, plus the two generic ones.
FRESHNESS_DIMENSIONS = ("dated_as_of", "current_facts", "git", "github", "gitea", "cloudflare")
# Older vault pages may carry these names. They stay valid in freshness_checked
# but have no extractor here.
LEGACY_DIMENSIONS = ("ticket_status", "ownership", "migration_progress", "confluence")
STATUS_CLAIM = re.compile(r"\b(?:in progress|open|opened|closed|done|resolved|planned|blocked|parked|complete(?:d)?|"
                          r"deployed|live|merged|released|archived|decommissioned)\b", re.I)
DATED_AS_OF = re.compile(r"\bas\s+of\s+(?:on\s+)?\d{4}-\d{2}-\d{2}\b", re.I)
CURRENT_CLAIM = re.compile(r"\b(?:currently|as of now|at the moment|today)\b", re.I)
GIT_CLAIM = re.compile(r"\b(?:branch|repo(?:sitory)?|commit|tag|release)\b", re.I)
GITHUB_REF = re.compile(r"github\.com[/:][\w.-]+/[\w.-]*\w", re.I)
GITEA_REF = re.compile(r"gitea[\w.-]*\.[a-z]+[/:][\w.-]+/[\w.-]*\w", re.I)
CLOUDFLARE_CLAIM = re.compile(r"\bcloudflare\b|\b(?:workers|pages)\.dev\b", re.I)
WIKILINK = re.compile(r"\[\[([^\]]*)\]\]")
SOURCE = re.compile(r"^source:\s*(.+)$", re.I)
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL)[A-Z0-9_]*|"
    r"(?:token|secret|api_key|password|credential))\s*[:=]\s*['\"]?([A-Za-z0-9_./+=-]{20,})['\"]?\s*$"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
TOKEN = re.compile(r"[a-z0-9]+")
STOP_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with"}
ORPHANS_BASELINE_PATH = brain_lib.CONFIG_PATH.parent / "orphans-baseline.json"


def mask_code(text: str) -> str:
    """Replace fenced and inline code with spaces while preserving line positions."""
    masked, fenced = [], False
    for line in text.splitlines(keepends=True):
        if "```" in line:
            masked.append("".join("\n" if char == "\n" else " " for char in line))
            fenced = not fenced
            continue
        if fenced:
            masked.append("".join("\n" if char == "\n" else " " for char in line))
            continue
        chars, inline = list(line), False
        for index, char in enumerate(chars):
            if char == "`":
                inline = not inline
                chars[index] = " "
            elif inline and char != "\n":
                chars[index] = " "
        masked.append("".join(chars))
    return "".join(masked)


def _paragraphs(text):
    """Yield non-code paragraphs with their source line number."""
    for match in re.finditer(r"(?:^|\n\s*\n)(.+?)(?=\n\s*\n|\Z)", mask_code(text), re.S):
        value = match.group(1)
        if value.strip():
            yield value, match.start(1) + 1


def _line_number(text, offset):
    return text.count("\n", 0, offset) + 1


def _sentences(paragraph):
    """Keep a ticket and its asserted state in the same sentence-sized context."""
    return (part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", paragraph) if part.strip())


def bounded_output(records, compact, verbose=False):
    """Apply one output policy for every check with an inventory.

    Four separate checks have emitted their full input before this helper existed.
    Default output must be compact or explicitly bounded, never silently truncated.
    """
    emitted = records if verbose else [compact(record) for record in records]
    return emitted, {"total": len(records), "emitted": len(emitted), "dropped": 0,
                     "format": "full" if verbose else "compact"}


def extract_claims(pages):
    """Extract mechanical candidates. A candidate is deliberately not a verdict.

    Only reading the carrying paragraph, then checking the live source with git,
    gh, tea or Cloudflare, settles one."""
    by_dimension = {name: [] for name in FRESHNESS_DIMENSIONS}
    for page in pages:
        if "parse_error" in page:
            continue
        for paragraph, offset in _paragraphs(page["body"]):
            line = _line_number(page["body"], offset)
            for sentence in _sentences(paragraph):
                common = {"file": page["file"], "slug": page["slug"], "line": line,
                          "text": " ".join(sentence.split())}
                if DATED_AS_OF.search(sentence):
                    by_dimension["dated_as_of"].append(dict(common))
                if CURRENT_CLAIM.search(sentence):
                    by_dimension["current_facts"].append(dict(common))
                if GIT_CLAIM.search(sentence) and STATUS_CLAIM.search(sentence):
                    by_dimension["git"].append(dict(common))
                refs = GITHUB_REF.findall(sentence)
                if refs:
                    by_dimension["github"].append({**common, "refs": sorted(set(refs))})
                refs = GITEA_REF.findall(sentence)
                if refs:
                    by_dimension["gitea"].append({**common, "refs": sorted(set(refs))})
                if CLOUDFLARE_CLAIM.search(sentence) and STATUS_CLAIM.search(sentence):
                    by_dimension["cloudflare"].append(dict(common))
    return by_dimension


def _compact_claim(item):
    excerpt = item["text"]
    if len(excerpt) > 80:
        excerpt = excerpt[:77].rstrip() + "..."
    return {"slug": item["slug"], "dimension": item["dimension"], "line": item["line"],
            "reference": next(iter(item.get("refs", [])), None), "excerpt": excerpt}


def claims(pages, verbose=False):
    """Report the candidates per dimension. Nothing is verified here.

    No dimension has a mechanical verifier, so every candidate stays open until
    the freshness skill reads it and checks the live source. An empty result is
    reported as such, and a non-empty one is never a pass."""
    candidates = extract_claims(pages)
    result = {}
    for dimension in FRESHNESS_DIMENSIONS:
        items = [{**item, "dimension": dimension} for item in candidates[dimension]]
        emitted, inventory = bounded_output(items, _compact_claim, verbose)
        result[dimension] = {
            "claims_found": len(items),
            "candidates": emitted,
            "candidate_output": inventory,
            "status": ("no verifiable claims present" if not items
                       else "candidates found, verify each against its live source"),
        }
    return {"dimensions": result, "claims_found": sum(len(v) for v in candidates.values())}


def write_freshness_checked(path, dimensions, checked_on=None):
    """Write only freshness_checked and re-parse before returning, per the write contract."""
    original = Path(path).read_text(encoding="utf-8")
    checked_on = checked_on or dt.date.today()
    frontmatter = re.match(r"^---\s*\n(.*?)\n---", original, re.S)
    existing = yaml.safe_load(frontmatter.group(1)) if frontmatter else {}
    prior = existing.get("freshness_checked") if isinstance(existing, dict) else None
    values = dict(prior) if isinstance(prior, dict) else {}
    values.update({dimension: checked_on for dimension in dimensions})
    mapping = ", ".join(f"{dimension}: {date.isoformat() if isinstance(date, (dt.date, dt.datetime)) else date}"
                        for dimension, date in values.items())
    replacement = f"freshness_checked: {{ {mapping} }}"
    if re.search(r"^freshness_checked:.*$", original, re.M):
        updated = re.sub(r"^freshness_checked:.*$", replacement, original, flags=re.M)
    else:
        updated = re.sub(r"^(---\s*\n)", r"\1" + replacement + "\n", original, count=1)
    try:
        parsed = yaml.safe_load(re.match(r"^---\s*\n(.*?)\n---", updated, re.S).group(1))
        if not isinstance(parsed, dict) or not valid_freshness_checked(parsed.get("freshness_checked")):
            raise ValueError("frontmatter no longer satisfies the schema")
    except Exception:
        Path(path).write_text(original, encoding="utf-8")
        raise
    Path(path).write_text(updated, encoding="utf-8")


def apply_claim_fix(path, stale_text, fresh_text, dimensions, checked_on=None):
    """Apply one exact, reviewed correction then record only its checked dimensions."""
    path = Path(path)
    original = path.read_text(encoding="utf-8")
    if original.count(stale_text) != 1:
        raise ValueError("stale text must occur exactly once")
    path.write_text(original.replace(stale_text, fresh_text), encoding="utf-8")
    try:
        write_freshness_checked(path, dimensions, checked_on)
    except Exception:
        path.write_text(original, encoding="utf-8")
        raise


def roots_from_args(values: list[str]) -> list[Path]:
    if values:
        return [Path(value).expanduser().resolve() for value in values]
    cfg = brain_lib.load_config()
    roots = [path.resolve() for path in brain_lib.config_roots(cfg)]
    if not roots:
        # Zero roots would make every check pass on zero pages.
        raise brain_lib.ConfigError(brain_lib.NOT_CONFIGURED)
    return roots


def iter_files(roots: list[Path]):
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root)
            if path.name in EXCLUDED_FILES or rel.parts[0] in EXCLUDED_DIRS:
                continue
            yield root, path, rel


def load_pages(roots: list[Path]):
    pages = []
    for root, path, rel in iter_files(roots):
        text = path.read_text(encoding="utf-8", errors="replace")
        item = {"root": str(root), "file": str(path), "relative_path": rel.as_posix(),
                "slug": path.stem, "text": text, "frontmatter": None, "body": ""}
        if not text.startswith("---\n"):
            item["parse_error"] = "missing frontmatter opening delimiter"
        else:
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
            if not match:
                item["parse_error"] = "missing frontmatter closing delimiter"
            else:
                try:
                    value = yaml.safe_load(match.group(1))
                    if not isinstance(value, dict):
                        item["parse_error"] = "frontmatter is not a mapping"
                    else:
                        item["frontmatter"] = value
                        item["body"] = match.group(2)
                except Exception as exc:
                    item["parse_error"] = str(exc)
        pages.append(item)
    return pages


def validate(pages):
    unparseable, findings = [], []
    for page in pages:
        if "parse_error" in page:
            unparseable.append({"file": page["file"], "error": page["parse_error"]})
            continue
        fm = page["frontmatter"]
        for key, typ in REQUIRED.items():
            if key not in fm:
                findings.append({"file": page["file"], "field": key, "error": "missing"})
            elif not isinstance(fm[key], typ):
                findings.append({"file": page["file"], "field": key,
                                 "error": "wrong type", "expected": typ.__name__,
                                 "actual": type(fm[key]).__name__})
        if "last_updated" not in fm:
            findings.append({"file": page["file"], "field": "last_updated", "error": "missing"})
        elif not isinstance(fm["last_updated"], (dt.date, dt.datetime)):
            findings.append({"file": page["file"], "field": "last_updated", "error": "not date-like"})
        if "freshness_checked" in fm and not valid_freshness_checked(fm["freshness_checked"]):
            findings.append({"file": page["file"], "field": "freshness_checked",
                             "error": "must be a dimensions mapping"})
    return {"unparseable": unparseable, "findings": findings, "page_count": len(pages),
            "ok": not unparseable and not findings}


def valid_freshness_checked(value):
    """Accept only the per-dimension marker: a mapping of dimension name to date."""
    if not isinstance(value, dict) or not value:
        return False
    return all(name in (*FRESHNESS_DIMENSIONS, *LEGACY_DIMENSIONS) and isinstance(date, (dt.date, dt.datetime))
               for name, date in value.items())


def malformed(pages):
    findings = []
    for page in pages:
        for number, line in enumerate(mask_code(page["text"]).splitlines(), 1):
            pos = 0
            while pos < len(line):
                opening, closing = line.find("[[", pos), line.find("]]", pos)
                if closing >= 0 and (opening < 0 or closing < opening):
                    findings.append({"file": page["file"], "line": number, "text": "]]", "error": "closing without opener"})
                    pos = closing + 2
                elif opening >= 0:
                    if closing < 0:
                        findings.append({"file": page["file"], "line": number, "text": line[opening:], "error": "opening without closing"})
                        break
                    text = line[opening + 2:closing]
                    if not text.strip():
                        findings.append({"file": page["file"], "line": number, "text": "[[]]", "error": "empty link text"})
                    if text.rstrip().endswith((".", "!", "?")):
                        findings.append({"file": page["file"], "line": number, "text": line[opening:closing + 2], "error": "punctuation inside link text"})
                    pos = closing + 2
                else:
                    break
    return {"findings": findings, "ok": not findings}


def links(pages, roots, verbose=False):
    slugs = {page["slug"] for page in pages}
    # external-slugs.txt lists pages of another vault, such as a work vault
    # cloned next to this one. They resolve where both vaults exist, so a
    # missing target named there is reported as external, not as dead.
    external = set()
    source_files = set()
    for root in roots:
        listed = root / "external-slugs.txt"
        if listed.exists():
            external.update(line.strip() for line in listed.read_text(encoding="utf-8").splitlines()
                            if line.strip() and not line.lstrip().startswith("#"))
        source = root / "sources"
        if source.exists():
            for path in source.rglob("*"):
                if path.is_file():
                    source_files.add(path.name)
                    source_files.add(path.relative_to(source).as_posix())
                    source_files.add((Path("sources") / path.relative_to(source)).as_posix())
    findings, external_links, all_links = [], [], []
    for page in pages:
        for number, line in enumerate(mask_code(page["text"]).splitlines(), 1):
            for match in WIKILINK.finditer(line):
                raw = match.group(1).strip()
                source_match = SOURCE.match(raw)
                if source_match:
                    target = source_match.group(1).strip()
                    resolved = target in source_files
                    kind = "source"
                else:
                    target = raw.split("|", 1)[0].split("#", 1)[0].strip()
                    resolved = target in slugs
                    kind = "wikilink"
                record = {"file": page["file"], "line": number, "text": match.group(0),
                          "kind": kind, "target": target, "resolved": resolved}
                all_links.append(record)
                if target and not resolved:
                    (external_links if kind == "wikilink" and target in external else findings).append(record)
    return {"links": all_links if verbose else findings, "dead": findings, "external": external_links,
            "scanned": len(all_links), "ok": not findings}


def _load_orphans_baseline(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("zero"), list):
        raise ValueError("baseline must contain a zero list")
    return {str(slug) for slug in value["zero"]}


def set_orphans_baseline(path, zero):
    path.parent.mkdir(parents=True, exist_ok=True)
    slugs = [item if isinstance(item, str) else item["slug"] for item in zero]
    path.write_text(json.dumps({"zero": sorted(slugs)}, indent=2) + "\n",
                    encoding="utf-8")


def orphans(pages, verbose=False, baseline_path=None):
    baseline_path = baseline_path or ORPHANS_BASELINE_PATH
    counts = Counter()
    for record in links(pages, [], verbose=True)["links"]:
        if record["kind"] == "wikilink" and record["target"]:
            counts[record["target"]] += 1
    inbound = [{"file": p["file"], "slug": p["slug"], "inbound": counts[p["slug"]]} for p in pages]
    zero = [item for item in inbound if item["inbound"] == 0]
    current = {item["slug"]: item for item in zero}
    baseline = _load_orphans_baseline(baseline_path)
    if baseline is None:
        return {"inbound": inbound if verbose else [], "zero": zero if verbose else sorted(current), "page_count": len(pages),
                "new": [], "resolved": [], "known": [], "unclassified": zero if verbose else sorted(current),
                "baseline": {"status": "absent", "recording_recommended": True}, "ok": True}
    new = [current[slug] for slug in sorted(current.keys() - baseline)]
    known = [current[slug] for slug in sorted(current.keys() & baseline)]
    resolved = sorted(baseline - current.keys())
    return {"inbound": inbound if verbose else [], "zero": zero if verbose else sorted(current), "page_count": len(pages),
            "new": new if verbose else [item["slug"] for item in new], "resolved": resolved,
            "known": known if verbose else [item["slug"] for item in known], "unclassified": [],
            "baseline": {"status": "present", "count": len(baseline)}, "ok": not new}


def slugs(pages):
    grouped = defaultdict(list)
    for page in pages:
        grouped[page["slug"]].append(page["file"])
    duplicates = [{"slug": slug, "files": files} for slug, files in grouped.items() if len(files) > 1]
    return {"duplicates": duplicates, "ok": not duplicates}


MUSTKNOW_MAX_LINES = 30
MUSTKNOW_MAX_CHARS = 160
HOOK_MODES = ("mustKnow",)
HOOK_HARD_LIMIT = 10000  # claude-code#44086: above this the payload becomes a 2KB preview
HOOK_BUDGET = 9000       # what session-start.sh targets, leaving headroom


def _topics(root: Path) -> dict:
    f = root / "topics.yaml"
    if not f.exists():
        return {}
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return {str(k): str(v).strip() for k, v in data.items() if str(v).strip()} if isinstance(data, dict) else {}


def glosses(pages, roots):
    """Topic coverage. Every wiki folder needs a one-line gloss in topics.yaml.

    The gloss heads a folder's results in `brain_lib.py find`, so an unglossed
    folder returns matches with no context about what the topic covers."""
    per_root, unglossed = [], []
    for root in roots:
        topics = _topics(root)
        folders = set()
        for page in pages:
            if page["root"] != str(root):
                continue
            rel = Path(page["relative_path"])
            # Only wiki trees carry topics. Flat files at a root (CLAUDE.md,
            # README.md, the generated indexes) are not pages and have no folder.
            if len(rel.parts) < 2 or not rel.parts[0].startswith("wiki"):
                continue
            folders.add(rel.parent.as_posix())
        missing = sorted(f for f in folders if f not in topics)
        unglossed += [{"root": root.name, "folder": f} for f in missing]
        per_root.append({"root": root.name, "folders": len(folders),
                         "glossed": len(folders) - len(missing),
                         "has_topics_yaml": bool(topics)})
    return {"roots": per_root, "unglossed": unglossed, "ok": not unglossed}


def mustknow(pages, roots):
    """Must-know discipline. These rules inject into every session, so the block
    stays a short list of imperatives rather than a second CLAUDE.md."""
    rules = []
    for page in pages:
        if roots and page["root"] not in {str(r) for r in roots}:
            continue
        fm = page.get("frontmatter") or {}
        raw = fm.get("must_know") or ""
        for entry in (raw if isinstance(raw, list) else [raw]):
            entry = str(entry).strip()
            if entry:
                rules.append({"slug": page["slug"], "chars": len(entry),
                              "over": len(entry) > MUSTKNOW_MAX_CHARS})
    long_rules = [r for r in rules if r["over"]]
    return {"mustknow_lines": len(rules), "mustknow_cap": MUSTKNOW_MAX_LINES,
            "over_length": long_rules, "max_chars": MUSTKNOW_MAX_CHARS,
            "ok": not long_rules and len(rules) <= MUSTKNOW_MAX_LINES}


_GROUND_STOP = {
    "a", "an", "and", "any", "are", "as", "at", "be", "because", "before", "but", "by",
    "can", "come", "comes", "do", "does", "for", "from", "has", "have", "in", "into",
    "is", "it", "its", "must", "need", "needs", "never", "no", "not", "of", "on", "one",
    "only", "or", "other", "own", "so", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "to", "use", "used", "uses", "using", "was",
    "were", "what", "when", "where", "which", "while", "will", "with", "without",
    "you", "your", "always", "also", "each", "every", "just", "make", "makes", "may",
    "might", "more", "most", "much", "should", "some", "such", "take", "takes", "them",
}
_WORD = re.compile(r"[A-Za-z0-9_./~$@-]{3,}")
GROUNDING_MAX_UNGROUNDED = 2


def _content_terms(text: str) -> list[str]:
    """Distinctive words in a piece of prose, lowercased, stopwords dropped."""
    out = []
    for token in _WORD.findall(text.lower()):
        token = token.strip("./-")
        if len(token) < 3 or token in _GROUND_STOP or token.isdigit():
            continue
        out.append(token)
    return out


def _covered(term: str, body: str) -> bool:
    """Does the body carry this term, allowing for simple morphology?

    Prefix matching on the first five characters absorbs commit/commits and
    repo/repos/repositories without pulling in a stemmer."""
    if term in body:
        return True
    stem = term[:5]
    return len(stem) >= 4 and re.search(r"\b" + re.escape(stem), body) is not None


def grounding(pages):
    """Is each must_know line actually supported by the page that carries it?

    A must_know line is injected verbatim into every session while the page it
    cites is not, so a rule can drift from its evidence and nothing notices. This
    happened: a rule said NOTICKET was acceptable under ~/sources while its own
    page said the pre-receive hook rejects it.

    Entailment is not mechanical. What is checkable is grounding: the distinctive
    words of the rule should appear on the page. That catches a rule asserting
    things the page never discusses, which is the common shape of the drift. It
    does NOT catch a rule that contradicts the page in its own vocabulary, so a
    clean result here is not proof the rule is right."""
    findings = []
    for page in pages:
        fm = page.get("frontmatter") or {}
        raw = fm.get("must_know") or ""
        rules = [str(r).strip() for r in (raw if isinstance(raw, list) else [raw]) if str(r).strip()]
        if not rules:
            continue
        body = (page.get("body") or "").lower()
        for rule in rules:
            ungrounded = sorted({t for t in _content_terms(rule) if not _covered(t, body)})
            if len(ungrounded) > GROUNDING_MAX_UNGROUNDED:
                findings.append({"file": page["file"], "slug": page["slug"],
                                 "rule": rule, "ungrounded": ungrounded})
    return {"findings": findings, "max_ungrounded": GROUNDING_MAX_UNGROUNDED,
            "ok": not findings}


# A phrase carries weight in proportion to its rarity, not to how many phrases
# happen to match. Requiring two matches instead would blind the check to any
# two-word slug, which produces exactly one phrase, and those name the most
# fundamental topics in the vault.
RESTATED_PHRASE_MAX_PAGES = 2   # a phrase used more widely than this is vocabulary
RESTATED_LIMIT = 30


def _slug_phrases(slug: str) -> list[tuple[str, re.Pattern]]:
    """Consecutive word pairs from a slug, as separator-tolerant patterns.

    Scattered single words are worthless here. Any two pages share generic slug
    words like actions or filters. An adjacent pair such as "email domains" or
    "istioctl analyze" is a topic, and that is what a restatement reuses."""
    words = [w for w in slug.lower().split("-") if len(w) > 3 and w not in _GROUND_STOP]
    out = []
    for a, b in zip(words, words[1:]):
        out.append((f"{a} {b}", re.compile(rf"\b{re.escape(a)}[\s\-_]+{re.escape(b)}\b")))
    return out


def restated(pages, verbose=False):
    """Pages discussing a topic another page owns, without linking to it.

    The vault schema says to prefer a link over repeating a fact, so one concept
    lives on exactly one page. Nothing enforced it, and pages ended up restating
    facts that had an owner, sometimes wrongly.

    ADVISORY, never a failure. Two pages can legitimately use the same phrase, and
    only reading tells you whether this one is a missing link, a merge, or fine as
    it stands. Treated as a lint failure it would just get muted."""
    bodies, phrases = {}, {}
    for page in pages:
        rel = Path(page["relative_path"])
        if len(rel.parts) < 2 or not rel.parts[0].startswith("wiki"):
            continue
        bodies[page["slug"]] = (page.get("body") or "").lower()
        phrases[page["slug"]] = _slug_phrases(page["slug"])
    if not bodies:
        return {"findings": [], "total": 0, "ok": None}

    findings = []
    for owner, owner_phrases in phrases.items():
        for label, pattern in owner_phrases:
            hits = [slug for slug, body in bodies.items()
                    if slug != owner and pattern.search(body)]
            if not hits or len(hits) > RESTATED_PHRASE_MAX_PAGES:
                continue                            # shared vocabulary, not a topic
            for slug in hits:
                if f"[[{owner}]]" in bodies[slug]:
                    continue
                findings.append({"page": slug, "owner": owner, "phrase": label,
                                 "owner_pinned": None})
    merged = {}
    for f in findings:
        key = (f["page"], f["owner"])
        merged.setdefault(key, []).append(f["phrase"])
    findings = [{"page": a, "owner": b, "phrases": sorted(set(v))}
                for (a, b), v in merged.items()]
    findings.sort(key=lambda f: (-len(f["phrases"]), f["page"], f["owner"]))
    shown = findings if verbose else findings[:RESTATED_LIMIT]
    return {"findings": shown, "total": len(findings),
            "omitted": len(findings) - len(shown),
            "phrase_max_pages": RESTATED_PHRASE_MAX_PAGES, "ok": None}


def payload(roots):
    """Run the SessionStart hook exactly as Claude Code does and measure each command.

    This is the check that the original 30071-char failure needed and did not have:
    the must-know rules silently never arrived, and nothing in the vault could tell."""
    hook = Path(__file__).resolve().parent.parent / "hooks" / "session-start.sh"
    if not hook.exists():
        return {"error": f"hook script not found at {hook}", "ok": False}
    modes = []
    for mode in HOOK_MODES:
        entry = {"mode": mode}
        try:
            run = subprocess.run(["bash", str(hook), mode], capture_output=True,
                                 text=True, timeout=30)
            context = json.loads(run.stdout)["hookSpecificOutput"]["additionalContext"]
        except Exception as exc:
            entry.update({"error": f"{type(exc).__name__}: {exc}", "ok": False})
            modes.append(entry)
            continue
        size = len(context)
        entry.update({"chars": size, "over_budget": size > HOOK_BUDGET,
                      "truncated_by_claude_code": size >= HOOK_HARD_LIMIT,
                      "self_clipped": "[clipped to fit" in context,
                      "ok": size < HOOK_HARD_LIMIT and "[clipped to fit" not in context})
        modes.append(entry)
    return {"modes": modes, "hard_limit": HOOK_HARD_LIMIT, "budget": HOOK_BUDGET,
            "combined": sum(m.get("chars", 0) for m in modes),
            "ok": all(m.get("ok") for m in modes)}


def secrets(pages):
    findings = []
    for page in pages:
        for number, line in enumerate(page["text"].splitlines(), 1):
            if PRIVATE_KEY.search(line):
                findings.append({"file": page["file"], "line": number, "kind": "private key block"})
            elif SECRET_ASSIGNMENT.search(line):
                findings.append({"file": page["file"], "line": number, "kind": "assignment-shaped secret value"})
    return {"findings": findings, "ok": not findings}


def index_check(pages, roots):
    common = Path(os.path.commonpath([str(root) for root in roots])) if roots else None
    common_index = common / "index.md" if common else None
    if common_index and common_index.exists() and common not in roots:
        actual = sum(1 for line in common_index.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("- [["))
        return {"pages": len(pages), "index_entries": actual,
                "roots": [{"root": str(common), "entries": actual}], "ok": len(pages) == actual}
    entries = []
    for root in roots:
        index = root / "index.md"
        count = sum(1 for line in index.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("- [[")) if index.exists() else 0
        entries.append({"root": str(root), "entries": count})
    expected, actual = len(pages), sum(item["entries"] for item in entries)
    return {"pages": expected, "index_entries": actual, "roots": entries, "ok": expected == actual}


def _words(value):
    return {word for word in TOKEN.findall(str(value).lower()) if word not in STOP_WORDS}


def _split_family(slug):
    match = re.match(r"^(.+?)(?:[-_](?:\d+|[a-z]{2,3}))$", slug)
    return match.group(1) if match else None


def candidates(pages, limit=25, threshold=20, omit_details=False):
    inbound = {item["slug"]: item["inbound"] for item in orphans(pages, verbose=True)["inbound"]}
    results = []
    total_pairs = 0
    for left, right in itertools.combinations(pages, 2):
        total_pairs += 1
        lf, rf = left.get("frontmatter") or {}, right.get("frontmatter") or {}
        a, b = left["slug"], right["slug"]
        left_family, right_family = _split_family(a), _split_family(b)
        if left_family and left_family == right_family:
            continue
        title_overlap = _words(lf.get("title", "")) & _words(rf.get("title", ""))
        summary_overlap = _words(lf.get("summary", "")) & _words(rf.get("summary", ""))
        score = len(title_overlap) * 3 + len(summary_overlap) * 2
        if score < threshold:
            continue
        def info(page, fm):
            return {"slug": page["slug"], "summary": fm.get("summary", ""), "folder": str(Path(page["relative_path"]).parent), "word_count": len(page["body"].split()), "inbound": inbound[page["slug"]]}
        disqualifiers = []
        if any(re.search(r"\b(index|set)\b", str(fm.get("summary", "")), re.I) for fm in (lf, rf)):
            disqualifiers.append("summary describes an index or set")
        results.append({"pair": [info(left, lf), info(right, rf)], "score": score,
                        "title_word_overlap": sorted(title_overlap),
                        "summary_word_overlap": sorted(summary_overlap),
                        "mechanical_disqualifiers": disqualifiers})
    results.sort(key=lambda item: (-item["score"], item["pair"][0]["slug"], item["pair"][1]["slug"]))
    if omit_details:
        return {"threshold": threshold, "total_pairs_considered": total_pairs,
                "qualifying_pairs": len(results), "pairs": [],
                "detail_omitted_for_size": True, "detail_subcommand": "candidates"}
    emitted = results[:limit]
    return {"threshold": threshold, "cap": limit, "total_pairs_considered": total_pairs,
            "qualifying_pairs": len(results), "dropped_by_cap": len(results) - len(emitted),
            "pairs": emitted}


def _wiki_path(path):
    value = Path(path)
    return value.suffix == ".md" and value.name not in EXCLUDED_FILES and not (set(value.parts) & EXCLUDED_DIRS)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)


def _first_commit_date(root):
    result = _git(root, "log", "--reverse", "--format=%cs")
    if result.returncode:
        return None
    first = next((line.strip() for line in result.stdout.splitlines() if line.strip()), None)
    return dt.date.fromisoformat(first) if first else None


def _git_changes(root, since):
    result = _git(root, "log", f"--since={since.isoformat()}", "--name-status", "--find-renames", "--format=")
    if result.returncode:
        return {"new": [], "edited": [], "renamed": [], "deleted": []}
    changes = {"new": [], "edited": [], "renamed": [], "deleted": []}
    seen = {key: set() for key in changes}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if not fields or not fields[0]:
            continue
        status = fields[0][0]
        if status == "R" and len(fields) >= 3:
            old, new = fields[1], fields[2]
            if _wiki_path(old) or _wiki_path(new):
                key = (old, new)
                if key not in seen["renamed"]:
                    changes["renamed"].append({"from": old, "to": new})
                    seen["renamed"].add(key)
            continue
        if len(fields) < 2 or not _wiki_path(fields[1]):
            continue
        kind = {"A": "new", "M": "edited", "D": "deleted"}.get(status)
        if kind and fields[1] not in seen[kind]:
            changes[kind].append(fields[1])
            seen[kind].add(fields[1])
    return changes


def _last_updated_changes(root, since):
    changed = []
    current = {}
    for _, path, rel in iter_files([root]):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
        value = None
        if match:
            try:
                value = (yaml.safe_load(match.group(1)) or {}).get("last_updated")
            except Exception:
                value = None
        date_value = value.date() if isinstance(value, dt.datetime) else value if isinstance(value, dt.date) else None
        current[rel.as_posix()] = date_value
        if date_value and date_value > since:
            changed.append(rel.as_posix())
    return sorted(changed), current


def changed(roots, since, verbose=False):
    reports = []
    for root in roots:
        first_commit = _first_commit_date(root)
        history_truncated = first_commit is None or first_commit > since
        git_changes = _git_changes(root, since)
        updated, current_dates = _last_updated_changes(root, since)
        if history_truncated:
            selected = {"new": [], "edited": updated, "renamed": [], "deleted": []}
            source_used = "last_updated"
            cross_check = {"status": "skipped",
                           "reason": "git history starts after the since date and cannot distinguish edits from the import"}
        else:
            selected = git_changes
            source_used = "git"
            git_current = set(git_changes["new"] + git_changes["edited"])
            git_current.update(item["to"] for item in git_changes["renamed"])
            mismatches = [
                {"relative_path": path, "last_updated": current_dates.get(path).isoformat() if current_dates.get(path) else None}
                for path in sorted(git_current)
                if not current_dates.get(path) or current_dates[path] <= since
            ]
            # Emit only bounded, meaningful findings. A list approaching the
            # whole vault signals that this check is misapplied, not that every
            # page deserves an individual finding.
            if len(mismatches) >= max(25, len(current_dates) // 2):
                cross_check = {"status": "suppressed",
                               "reason": "mismatch count approaches the full vault and is not a meaningful finding list"}
            else:
                cross_check = {"status": "ran", "mismatches": mismatches}
        report = {"root": str(root), "first_commit_date": first_commit.isoformat() if first_commit else None,
                  "source_used": source_used, "history_truncated": history_truncated,
                  **selected, "cross_check": cross_check}
        if verbose:
            report["git_changes"] = git_changes
            report["last_updated_changes"] = updated
        reports.append(report)
    return {"since": since.isoformat(), "repositories": reports}


CHECKS = {"validate": lambda p, r, v=False: validate(p), "links": lambda p, r, v=False: links(p, r, v), "malformed": lambda p, r, v=False: malformed(p),
          "orphans": lambda p, r, v=False: orphans(p, v), "slugs": lambda p, r, v=False: slugs(p), "glosses": lambda p, r, v=False: glosses(p, r), "mustknow": lambda p, r, v=False: mustknow(p, r), "payload": lambda p, r, v=False: payload(r),
          "grounding": lambda p, r, v=False: grounding(p), "restated": lambda p, r, v=False: restated(p, v),
          "secrets": lambda p, r, v=False: secrets(p), "index": lambda p, r, v=False: index_check(p, r), "candidates": lambda p, r, v=False: candidates(p)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=[*CHECKS, "claims", "changed", "all"])
    parser.add_argument("roots", nargs="*", help="explicit vault roots, for testing or detached runs")
    parser.add_argument("--verbose", action="store_true", help="include full inventories")
    parser.add_argument("--set-baseline", action="store_true", help="record current orphan set as the baseline")
    parser.add_argument("--since", help="date window for the changed subcommand, YYYY-MM-DD")
    args = parser.parse_args(argv)
    if args.set_baseline and args.command not in {"orphans", "all"}:
        parser.error("--set-baseline requires orphans or all")
    try:
        roots = roots_from_args(args.roots)
    except brain_lib.ConfigError as e:
        print(f"brain_check: {e}", file=sys.stderr)
        return 1
    if args.command == "changed":
        if not args.since:
            parser.error("changed requires --since YYYY-MM-DD")
        try:
            since = dt.date.fromisoformat(args.since)
        except ValueError:
            parser.error("--since must be YYYY-MM-DD")
        result = {"changed": changed(roots, since, args.verbose)}
    else:
        pages = load_pages(roots)
    if args.command == "all":
        result = {name: func(pages, roots, args.verbose) for name, func in CHECKS.items() if name != "candidates"}
        result["candidates"] = candidates(pages, limit=25, omit_details=not args.verbose)
    elif args.command == "claims":
        result = {"claims": claims(pages, verbose=args.verbose)}
    elif args.command != "changed":
        result = {args.command: CHECKS[args.command](pages, roots, args.verbose)}
    if args.set_baseline:
        orphan_result = result["orphans"]
        set_orphans_baseline(ORPHANS_BASELINE_PATH, orphan_result["zero"])
        recorded = orphans(pages, args.verbose, ORPHANS_BASELINE_PATH)
        recorded["baseline"] = {"status": "recorded", "count": len(orphan_result["zero"])}
        result["orphans"] = recorded
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
