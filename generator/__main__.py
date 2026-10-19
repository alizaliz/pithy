"""One run: pick seeds → fetch real quotes → curate → draw → write the site.

Entries are made every few hours from a local start time (`slot_start` and
`every_hours` in seeds.toml), with ids of the slot's local date and start time,
like 2026-10-20-1745. Older entries have ids like 2026-10-12-am or 2026-10-11.

Usage:
  python -m generator              # make the current entry (skips if it exists)
  python -m generator --force      # regenerate the current entry
  python -m generator --offline    # no network/LLM: baked quotes + procedural image
  python -m generator --date 2026-10-20 --slot 1745
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


def parse_hhmm(value: str) -> dt.timedelta:
    h, m = value.split(":")
    return dt.timedelta(hours=int(h), minutes=int(m))


DAY = dt.timedelta(days=1)


def slot_times(start: dt.timedelta, every: dt.timedelta) -> list[str]:
    """Local start times of the day's slots as HHMM, e.g. 11:45 every 6h → 0545, 1145, 1745, 2345."""
    times = sorted((start + k * every) % DAY for k in range(DAY // every))
    return [f"{t.seconds // 3600:02d}{t.seconds // 60 % 60:02d}" for t in times]


def entry_id(now: dt.datetime, date: str | None = None, slot: str | None = None,
             start: dt.timedelta = dt.timedelta(0), every: dt.timedelta = DAY) -> str:
    """Id of the entry for local time `now`: the date and local start time of its slot,
    e.g. 2026-10-20-1745. Or an explicit date/slot (slot defaults to the first of the day).

    Slots begin at `start` and every `every` after, so a run that starts late still
    lands in the slot it was scheduled for.
    """
    if date:
        return f"{date}-{slot or slot_times(start, every)[0]}"
    shifted = now.replace(tzinfo=None) - start
    midnight = shifted.replace(hour=0, minute=0, second=0, microsecond=0)
    begins = midnight + (shifted - midnight) // every * every + start
    return begins.strftime("%Y-%m-%d-%H%M")


def scheduled_for_now(cron: str, now_utc: dt.datetime, tz: ZoneInfo,
                      start: dt.timedelta, every: dt.timedelta) -> bool:
    """Whether the cron that fired ("M H1,H2,... * * *", in UTC) was due at a local slot time.

    The workflow has one cron for each UTC offset (daylight time or not); only the
    one matching the timezone's current offset should make an entry.
    """
    minute, hours = cron.split()[:2]
    due = max(t for h in hours.split(",") for day in (0, 1)
              if (t := now_utc.replace(hour=int(h), minute=int(minute), second=0, microsecond=0)
                  - dt.timedelta(days=day)) <= now_utc)
    return due.astimezone(tz).strftime("%H%M") in slot_times(start, every)


def attribution(entry: dict) -> str:
    return f"{entry['author']}, {entry['work']}" if entry.get("work") else entry["author"]


def render_site(entry: dict) -> None:
    quote = f"“{entry['text']}”"
    page = TEMPLATE.read_text(encoding="utf-8")
    fields = {
        "TITLE": "pithy",
        "DESCRIPTION": f"{' '.join(entry['text'].split())} — {attribution(entry)}",
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
    ap.add_argument("--slot", help="a slot start time as HHMM, e.g. 1745 (with --date)")
    ap.add_argument("--cron", help="the schedule that triggered this run; skip if it isn't a slot time")
    args = ap.parse_args(argv)

    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    sched = cfg.get("schedule", {})
    tz = ZoneInfo(sched.get("timezone", "UTC"))
    start = parse_hhmm(sched.get("slot_start", "00:00"))
    every = dt.timedelta(hours=float(sched.get("every_hours", 24)))
    if args.slot and args.slot not in slot_times(start, every):
        ap.error(f"--slot must be one of {', '.join(slot_times(start, every))}")
    if args.cron and not scheduled_for_now(args.cron, dt.datetime.now(dt.timezone.utc), tz, start, every):
        print(f"Skipping: {args.cron!r} isn't a slot time in {tz.key} today (the other cron is).")
        return 0
    eid = entry_id(dt.datetime.now(tz), args.date, args.slot, start, every)

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "images").mkdir(exist_ok=True)
    entry_path = DATA / f"{eid}.json"
    if entry_path.exists() and not args.force:
        print(f"{eid} already generated; use --force to redo.")
        return 0

    history: list[dict] = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    for h in history:
        h.setdefault("id", h["date"])  # entries from before slots
    used = {h["hash"] for h in history}  # includes this entry's, so --force picks something new
    history = [h for h in history if h["id"] != eid]
    recent = history[-int(sched.get("avoid_recent", 6)):]

    # Normal runs are reproducible; --force reseeds so a rerun gives a different result.
    rng = random.Random(f"{eid}-{len(history)}" + (f"-{random.random()}" if args.force else ""))
    sentiment = weighted_pick(cfg["sentiments"], rng, {h["sentiment"] for h in recent}, "name")
    use_baked = args.offline or rng.random() < float(sched.get("baked_probability", 0.2))
    # Only sources tagged with the sentiment; with none, the baked list is used.
    matching = [s for s in cfg["sources"] if sentiment["name"] in s.get("sentiments", [])]
    source = None if use_baked or not matching else weighted_pick(matching, rng, {h.get("source") for h in recent}, "author")
    print(f"{eid}: sentiment={sentiment['name']!r} source={(source or {}).get('author', 'baked list')!r}")

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
    img_rel = f"data/images/{eid}.svg"
    (SITE / img_rel).write_text(svg, encoding="utf-8")

    entry = {
        "id": eid,
        "date": eid[:10],
        "slot": eid[11:],
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
    (DATA / "latest.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    history.append({"id": eid, "date": eid[:10], "hash": quote_hash(pick["text"]), "sentiment": sentiment["name"],
                    "source": source["author"] if source else "baked"})
    history.sort(key=lambda h: h["id"])
    HISTORY.write_text(json.dumps(history, indent=1) + "\n", encoding="utf-8")
    render_site(entry)
    print(f"  wrote {entry_path.relative_to(ROOT)} and site/index.html ({method} image)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
