#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["html2text"]
# ///
"""Fetch a URL and extract its readable HTML body as Markdown."""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import html2text


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
REMOVED_REGIONS = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1\s*>", re.I | re.S)
H1 = re.compile(r"^# (.+?)\s*$")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
END_MARKERS = {"related tags", "subscribe to receive notifications of new posts", "comments", "share this post"}
LINK_ONLY = re.compile(r"^(?:\[[^\]]+\]\([^)]*\)|<https?://[^>]+>|[-*+|0-9.()\s])+$")


def markdown(html):
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    return converter.handle(REMOVED_REGIONS.sub("", html)).strip()


def link_heavy(line):
    stripped = line.strip()
    return not stripped or bool(LINK_ONLY.fullmatch(stripped))


def title_from_html(html):
    match = re.search(r"<title\b[^>]*>(.*?)</title\s*>", html, re.I | re.S)
    if not match:
        return None
    return " ".join(unescape(re.sub(r"<[^>]+>", "", match.group(1))).split()) or None


def byline_from_html(html):
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(r"\bname\s*=\s*([\"'])author\1", tag, re.I):
            match = re.search(r"\bcontent\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S)
            if match:
                return unescape(match.group(2)).strip() or None
    return None


def slice_post(full_text):
    lines = full_text.splitlines()
    start = next((index for index, line in enumerate(lines) if H1.match(line)), None)
    if start is None:
        return full_text, "no_h1_found", True
    end = len(lines)
    for index in range(start + 1, len(lines)):
        heading = HEADING.match(lines[index])
        if heading and heading.group(1).strip().casefold() in END_MARKERS:
            end = index
            break
    method = "h1_to_end_marker" if end < len(lines) else "h1_to_document_end"
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines[start:end])).strip()
    return body, method, False


def extract_html(html):
    full_text = markdown(html)
    body, method, missing_h1 = slice_post(full_text)
    first_heading = H1.search(body)
    title = first_heading.group(1) if first_heading else title_from_html(html)
    result = {"title": title, "byline": byline_from_html(html), "markdown": body,
              "char_count": len(body), "extraction_method": method}
    lines = [line for line in body.splitlines() if line.strip()]
    link_lines = sum(link_heavy(line) for line in lines)
    if missing_h1 or len(body) < 500 or (lines and link_lines > len(lines) / 2):
        reason = "no H1 heading found" if missing_h1 else "body is too short or dominated by links"
        result["warning"] = f"extraction looks wrong: {reason}"
        result["full_page_text"] = full_text
    return result


def fetch(url):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            if status < 200 or status >= 400:
                raise RuntimeError(f"HTTP {status} while fetching {url}")
            charset = response.headers.get_content_charset() or "utf-8"
            return response.geturl(), status, response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"fetch failed for {url}: {exc.reason}") from exc


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: brain_fetch.py <url>", file=sys.stderr)
        return 2
    url = argv[0]
    try:
        final_url, status, html = fetch(url)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = {"url": final_url, "http_status": status, **extract_html(html)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
