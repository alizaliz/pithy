"""Quote candidates: Wikiquote pages and the baked list."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

WIKIQUOTE_API = "https://en.wikiquote.org/w/api.php"
USER_AGENT = "pithy-bot/1.0 (https://github.com/alizaliz/pithy)"

# Aphorisms are short. Override these in seeds.toml [selection].
MIN_LEN, MAX_LEN, MAX_WORDS = 15, 200, 35

# "Paul: some line" — a speaker label means dialogue, not an aphorism.
SPEAKER_LABEL = re.compile(r"^[A-Z][\w .'-]{0,30}:\s")

# '"…," said Nobby' / 'asked Granny' / '“…” “…”': narrated dialogue or an exchange.
SPEECH_TAG = re.compile(r"[\"“”‘’'],?\s+(?:said|says|asked|replied|added|whispered|shouted|muttered)\b"
                        r"|\b(?:said|asked|replied)\s+(?:the\s+)?[A-Z]|[”\"]\s+[“\"]")

# Film page sections that aren't one character, so their lines credit the film alone.
NOT_A_CHARACTER = re.compile(r"^(?:others?|voice-?overs?|narration|quotes|misc\w*|general)$", re.I)

# Trailing notes that aren't part of the quote: "(1640)", "(14th century)",
# "(Simpson, 2009)", "(Hays translation)", "(p. 12)".
TRAILING_NOTE = re.compile(r"\s*\((?:[^()]*\b\d{3,4}s?\b[^()]*|[^()]*\bcentury\b[^()]*|[^()]*\btranslat\w*[^()]*|pp?\. ?\d[^()]*)\)\.?$", re.I)

# On proverb pages, "(Francis Bacon)" credits a person, so the line isn't a proverb.
CREDITED = re.compile(r"\([A-Z][^()\d]*\)\.?$")

# A `**` line that cites a source rather than translating the quote above it.
CITATION = re.compile(r"\b1\d{3}\b|\b20\d{2}\b|^\s*(?:''(?!')|\")|\b(?:ch(?:apter)?|vol|p|pp|letter|book)\b\.?\s*\d", re.I)

# Common English words that don't double as German/French/Latin/Spanish ones.
ENGLISH_WORDS = {"the", "is", "and", "of", "to", "you", "your", "that", "this", "it", "not", "are",
                 "what", "who", "when", "he", "his", "with", "without", "for", "from", "be", "but",
                 "we", "our", "they", "has", "have", "can", "all", "only", "my", "do", "does", "no",
                 "there", "by", "at", "or", "if", "one", "than", "more", "never", "always", "nothing"}

# Level-2 sections whose bullets are NOT reliable quotes by the subject.
SKIP_SECTIONS = re.compile(
    r"disputed|misattributed|attributed|quotes? about|about |external links|see also|"
    r"sources|references|further reading|criticism|cast|taglines?|dialogue|questions",
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


def _looks_english(text: str) -> bool:
    return bool(ENGLISH_WORDS & set(re.findall(r"[a-z]+", text.lower())))


def _translation(lines: list[str], i: int) -> str | None:
    """The `**` line after line i, if it reads as an English translation, not a citation."""
    if i + 1 >= len(lines):
        return None
    m = re.match(r"^\*\*(?![*:])\s*(.+)$", lines[i + 1])
    if not m:
        return None
    raw = re.sub(r"^\s*(?:'')?Translation(?:'')?\s*:\s*", "", m.group(1), flags=re.I)
    text = clean_wikitext(raw)
    if _looks_english(text) and not CITATION.search(raw):
        return raw
    return None


def _english_version(body: str, lines: list[str], i: int) -> str:
    """Bullet i as given, or its English translation if it's in another language.

    Italics or no common English words suggest the original language, so the
    next `**` line is used if it reads as a translation. Without one, italic
    non-English bullets become "" (skipped); plain ones are kept as terse English.
    """
    plain = clean_wikitext(body)
    italic = re.fullmatch(r"''(?!').+''[.!?]?", re.sub(r"<ref.*|\[https?://[^\]]*\]", "", body).strip())
    if not italic and _looks_english(plain):
        return body
    return _translation(lines, i) or ("" if italic and not _looks_english(plain) else body)


def _lone_dialogue_line(lines: list[str], i: int) -> re.Match | None:
    """A `:'''Name''': line` that is a whole dialogue block on its own (a monologue).

    group(1) is the speaker, group(2) the line."""
    m = re.match(r"^:+\s*'''([^']+?)'''\s*:\s*(.+)$", lines[i])
    if not m:
        return None
    neighbours = [lines[j] for j in (i - 1, i + 1) if 0 <= j < len(lines)]
    return None if any(n.startswith(":") for n in neighbours) else m


def _film_text(text: str) -> str:
    """Drop [stage directions] and quote marks wrapped around the whole line."""
    text = re.sub(r"\s*\[[^\]]*\]\s*", " ", text).strip()
    wrapped = re.fullmatch(r"[\"“](.+)[\"”]", text)
    if wrapped and not re.search(r"[\"“”]", wrapped.group(1)):
        text = wrapped.group(1).strip()
    return text


def _character(heading: str) -> str | None:
    """'Chihiro/Sen' → 'Chihiro'; generic sections like 'Others' → None."""
    name = re.sub(r"\s*\([^)]*\)", "", clean_wikitext(heading)).split("/")[0].strip()
    return None if not name or NOT_A_CHARACTER.match(name) else name


def parse_wikiquote(wikitext: str, **options) -> list[str]:
    """The quote texts from a Wikiquote page; see parse_quotes for the options."""
    return [text for text, _ in parse_quotes(wikitext, **options)]


def parse_quotes(wikitext: str, proverbs: bool = False, max_len: int = MAX_LEN,
                 max_words: int = MAX_WORDS, section: str | None = None,
                 film: bool = False) -> list[tuple[str, str | None]]:
    """(text, speaker) for top-level `* ` bullets outside skipped sections
    (`**` lines are citations). The speaker is only known on film pages.

    Bullets in another language are replaced by their English translation.

    proverbs=True: when a line starts with a bold phrase, keep only that phrase
    (proverb pages often bold the proverb and follow it with an explanation).

    section: only use bullets under a level-3 heading containing this text
    (e.g. one book on an author's page).

    film=True: also take one-line dialogue blocks (including in the Dialogue
    section, which is otherwise skipped), and drop stage directions. The
    speaker is the character section a line is in, or its speaker label.
    """
    quotes: list[tuple[str, str | None]] = []
    skipping = in_dialogue = False
    h3 = ""
    character = None
    lines = wikitext.splitlines()
    for i, line in enumerate(lines):
        h2 = re.match(r"^==([^=].*?)==\s*$", line)
        if h2:
            in_dialogue = film and bool(re.search(r"dialogue", h2.group(1), re.I))
            skipping, h3 = bool(SKIP_SECTIONS.search(h2.group(1))) and not in_dialogue, ""
            character = _character(h2.group(1)) if film and not in_dialogue else None
            continue
        heading3 = re.match(r"^===([^=].*?)===\s*$", line)
        if heading3:
            h3 = clean_wikitext(heading3.group(1))
            continue
        if skipping or (section and section.lower() not in h3.lower()):
            continue
        speaker = character
        m = None if in_dialogue else re.match(r"^\*(?![*:])\s*(.+)$", line)
        if m:
            body = m.group(1).strip()
        elif film and (lone := _lone_dialogue_line(lines, i)):
            speaker, body = _character(lone.group(1)), lone.group(2).strip()
        else:
            continue
        label = re.match(r"^'''([^']+?)'''\s*:\s*", body) if film else None
        if label:  # "'''Agent Halpern''': line" in a section like "Other"
            speaker, body = _character(label.group(1)), body[label.end():]
        bold = re.match(r"^'''(.+?)'''", body)
        if proverbs and bold:
            body = bold.group(1)
        elif not proverbs:
            body = _english_version(body, lines, i)
        text = clean_wikitext(body)
        if film:
            text = _film_text(text)
        if not text or SPEAKER_LABEL.match(text) or SPEECH_TAG.search(text):
            continue
        while TRAILING_NOTE.search(text):
            text = TRAILING_NOTE.sub("", text)
        if proverbs and CREDITED.search(text):
            continue
        if (MIN_LEN <= len(text) <= max_len and len(text.split()) <= max_words
                and not text.endswith(":") and "{" not in text and _mostly_latin(text)):
            quotes.append((text, speaker))
    seen: set[str] = set()
    return [q for q in quotes if not (q[0] in seen or seen.add(q[0]))]


def wikiquote_candidates(source: dict, selection: dict | None = None) -> list[dict]:
    """Candidates from a source. Film lines credit "Character, Film" when the
    character is known, otherwise just the film (the source's `author`)."""
    selection = selection or {}
    film = source.get("kind") == "film"
    quotes = parse_quotes(
        fetch_wikitext(source["wikiquote"]),
        proverbs=source.get("kind") == "proverbs",
        max_len=int(selection.get("max_chars", MAX_LEN)),
        max_words=int(selection.get("max_words", MAX_WORDS)),
        section=source.get("section"),
        film=film,
    )
    origin = f"https://en.wikiquote.org/wiki/{urllib.parse.quote(source['wikiquote'].replace(' ', '_'))}"
    return [
        {"text": t, "author": speaker, "work": source["author"], "origin": origin} if film and speaker else
        {"text": t, "author": source["author"], "work": None if film else source.get("work"), "origin": origin}
        for t, speaker in quotes
    ]


def baked_candidates(path: Path, sentiment: str | None) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    matching = [q for q in items if sentiment in q.get("sentiments", [])]
    chosen = matching or items
    return [{"text": q["text"], "author": q["author"], "work": q.get("work"), "origin": "baked"} for q in chosen]
