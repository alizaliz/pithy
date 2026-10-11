"""Quote candidates: Wikiquote pages and the baked list."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

WIKIQUOTE_API = "https://en.wikiquote.org/w/api.php"
USER_AGENT = "pithy-bot/1.0 (https://github.com/; personal project)"

# Aphorisms are short. Override these in seeds.toml [selection].
MIN_LEN, MAX_LEN, MAX_WORDS = 15, 200, 35

# "Paul: some line" — a speaker label means dialogue, not an aphorism.
SPEAKER_LABEL = re.compile(r"^[A-Z][\w .'-]{0,30}:\s")

# Level-2 sections whose bullets are NOT reliable quotes by the subject.
SKIP_SECTIONS = re.compile(
    r"disputed|misattributed|attributed|quotes? about|about |external links|see also|"
    r"sources|references|further reading|criticism|cast|taglines|dialogue",
    re.I,
)


def fetch_wikitext(page: str) -> str:
    params = urllib.parse.urlencode({
        "action": "parse", "page": page, "prop": "wikitext",
        "format": "json", "formatversion": "2", "redirects": "1",
    })
    req = urllib.request.Request(f"{WIKIQUOTE_API}?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if "error" in data:
        raise RuntimeError(f"Wikiquote: {data['error'].get('info')}")
    return data["parse"]["wikitext"]


def clean_wikitext(s: str) -> str:
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    prev = None
    while prev != s:  # strip (possibly nested) templates
        prev = s
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:File|Image|Category):[^\]]*\]\]", "", s, flags=re.I)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\]", "", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    latin = sum(c.isascii() or "À" <= c <= "ɏ" for c in letters)
    return bool(letters) and latin / len(letters) > 0.9


def parse_wikiquote(wikitext: str, proverbs: bool = False, max_len: int = MAX_LEN,
                    max_words: int = MAX_WORDS) -> list[str]:
    """Top-level `* ` bullets outside skipped sections (`**` lines are citations).

    proverbs=True: when a line starts with a bold phrase, keep only that phrase
    (proverb pages often bold the proverb and follow it with an explanation).
    """
    quotes: list[str] = []
    skipping = False
    for line in wikitext.splitlines():
        h2 = re.match(r"^==([^=].*?)==\s*$", line)
        if h2:
            skipping = bool(SKIP_SECTIONS.search(h2.group(1)))
            continue
        if skipping:
            continue
        m = re.match(r"^\*(?![*:])\s*(.+)$", line)
        if not m:
            continue
        body = m.group(1).strip()
        bold = re.match(r"^'''(.+?)'''", body)
        if proverbs and bold:
            body = bold.group(1)
        if SPEAKER_LABEL.match(clean_wikitext(body)):
            continue
        text = clean_wikitext(body)
        if (MIN_LEN <= len(text) <= max_len and len(text.split()) <= max_words
                and not text.endswith(":") and "{" not in text and _mostly_latin(text)):
            quotes.append(text)
    seen: set[str] = set()
    return [q for q in quotes if not (q in seen or seen.add(q))]


def wikiquote_candidates(source: dict, selection: dict | None = None) -> list[dict]:
    selection = selection or {}
    texts = parse_wikiquote(
        fetch_wikitext(source["wikiquote"]),
        proverbs=source.get("kind") == "proverbs",
        max_len=int(selection.get("max_chars", MAX_LEN)),
        max_words=int(selection.get("max_words", MAX_WORDS)),
    )
    return [
        {"text": t, "author": source["author"], "work": source.get("work"),
         "origin": f"https://en.wikiquote.org/wiki/{urllib.parse.quote(source['wikiquote'].replace(' ', '_'))}"}
        for t in texts
    ]


def baked_candidates(path: Path, sentiment: str | None) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    matching = [q for q in items if sentiment in q.get("sentiments", [])]
    chosen = matching or items
    return [{"text": q["text"], "author": q["author"], "work": q.get("work"), "origin": "baked"} for q in chosen]
