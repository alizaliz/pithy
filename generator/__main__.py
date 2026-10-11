"""Daily run: pick seeds → fetch real quotes → curate → draw → write the site.

Usage:
  python -m generator              # normal daily run (skips if today exists)
  python -m generator --force      # regenerate today's entry
  python -m generator --offline    # no network/LLM: baked quotes + procedural image
  python -m generator --date 2026-10-12
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import random
import sys
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from . import curator, image, sources
from .llm import LLM

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "seeds.toml"
BAKED = ROOT / "config" / "baked_quotes.json"
SITE = ROOT / "site"
DATA = SITE / "data"
HISTORY = DATA / "history.json"
TEMPLATE = Path(__file__).with_name("template.html")


def quote_hash(text: str) -> str:
    norm = "".join(ch for ch in text.lower() if ch.isalnum())
    return hashlib.sha1(norm.encode()).hexdigest()[:12]


def weighted_pick(items: list[dict], rng: random.Random, exclude: set[str], key: str) -> dict:
    pool = [i for i in items if i[key] not in exclude] or items
    return rng.choices(pool, weights=[float(i.get("weight", 1)) for i in pool], k=1)[0]


class OfflineLLM:
    """Stand-in used with --offline: picks the first candidate, image falls back."""

    def chat(self, system: str, user: str, temperature: float = 0.7) -> str:
        if "Reply with JSON" in system:
            return '{"index": 0, "visual_brief": "A sextant drawn in elevation with dimension lines", "alt": "Technical drawing of a sextant"}'
        raise RuntimeError("offline")


def attribution(entry: dict) -> str:
    return f"{entry['author']}, {entry['work']}" if entry.get("work") else entry["author"]


def render_site(entry: dict) -> None:
    quote = f"“{entry['text']}”"
    page = TEMPLATE.read_text(encoding="utf-8")
    fields = {
        "TITLE": "pithy",
        "DESCRIPTION": f"{entry['text']} — {attribution(entry)}",
        "IMAGE": entry["image"],
        "ALT": entry["alt"],
        "QUOTE": quote,
        "ATTRIBUTION": attribution(entry),
    }
    for k, v in fields.items():
        page = page.replace("{{" + k + "}}", html.escape(v, quote=True))
    (SITE / "index.html").write_text(page, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--date")
    args = ap.parse_args(argv)

    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    sched = cfg.get("schedule", {})
    tz = ZoneInfo(sched.get("timezone", "UTC"))
    today = args.date or dt.datetime.now(tz).date().isoformat()

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "images").mkdir(exist_ok=True)
    entry_path = DATA / f"{today}.json"
    if entry_path.exists() and not args.force:
        print(f"{today} already generated; use --force to redo.")
        return 0

    history: list[dict] = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    history = [h for h in history if h["date"] != today]
    used = {h["hash"] for h in history}
    recent = history[-int(sched.get("avoid_recent_days", 3)):]

    rng = random.Random(f"{today}-{len(history)}")
    sentiment = weighted_pick(cfg["sentiments"], rng, {h["sentiment"] for h in recent}, "name")
    use_baked = args.offline or rng.random() < float(sched.get("baked_probability", 0.2))
    source = None if use_baked else weighted_pick(cfg["sources"], rng, {h.get("source") for h in recent}, "author")
    print(f"{today}: sentiment={sentiment['name']!r} source={(source or {}).get('author', 'baked list')!r}")

    candidates: list[dict] = []
    if source:
        try:
            candidates = sources.wikiquote_candidates(source, cfg.get("selection"))
            print(f"  {len(candidates)} candidates from Wikiquote")
        except Exception as e:  # noqa: BLE001
            print(f"  Wikiquote failed ({e}); using baked list")
    candidates = [c for c in candidates if quote_hash(c["text"]) not in used]
    if len(candidates) < 5:
        source = None
        candidates = [c for c in sources.baked_candidates(BAKED, sentiment["name"]) if quote_hash(c["text"]) not in used]
        if not candidates:  # everything used: allow repeats from the baked list
            candidates = sources.baked_candidates(BAKED, sentiment["name"])

    llm = OfflineLLM() if args.offline else LLM.from_env()
    pick = curator.curate(llm, sentiment, candidates, rng)
    print(f"  quote: {pick['text'][:70]}… — {attribution(pick)}")
    print(f"  brief: {pick['visual_brief']}")

    svg, method = image.generate_svg(llm, pick["visual_brief"])
    img_rel = f"data/images/{today}.svg"
    (SITE / img_rel).write_text(svg, encoding="utf-8")

    entry = {
        "date": today,
        "text": pick["text"],
        "author": pick["author"],
        "work": pick.get("work"),
        "origin": pick["origin"],
        "sentiment": sentiment["name"],
        "visual_brief": pick["visual_brief"],
        "alt": pick["alt"],
        "image": img_rel,
        "image_method": method,
    }
    entry_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DATA / "today.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    history.append({"date": today, "hash": quote_hash(pick["text"]), "sentiment": sentiment["name"],
                    "source": source["author"] if source else "baked"})
    history.sort(key=lambda h: h["date"])
    HISTORY.write_text(json.dumps(history, indent=1) + "\n", encoding="utf-8")
    render_site(entry)
    print(f"  wrote {entry_path.relative_to(ROOT)} and site/index.html ({method} image)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
