import unittest
import unittest.mock

import datetime as dt
import json
import tomllib

from generator import image, sources
from zoneinfo import ZoneInfo

from generator.__main__ import BAKED, CONFIG, entry_id, scheduled_for_now, slot_times
from generator.llm import extract_json, extract_svg

WIKITEXT = """{{Wikipedia}}
'''Friedrich Nietzsche''' was a German philosopher.

== Quotes ==
=== Human, All Too Human (1878) ===
* Convictions are more dangerous foes of truth than lies.<ref>Aphorism 483</ref>
** Aphorism 483, as translated by [[w:R. J. Hollingdale|R. J. Hollingdale]]
* He who has a ''why'' to live can bear almost any [[how]].
** Short
* Too short.

== Disputed ==
* This one is disputed and should never be returned at all.

== Quotes about Nietzsche ==
* Someone else talking about Nietzsche at some length here.

== Later works ==
* And if you gaze long into an abyss, the abyss also gazes into you.
"""


class WikiquoteParsing(unittest.TestCase):
    def test_parses_top_level_quotes_and_skips_sections(self):
        q = sources.parse_wikiquote(WIKITEXT)
        self.assertEqual(q, [
            "Convictions are more dangerous foes of truth than lies.",
            "He who has a why to live can bear almost any how.",
            "And if you gaze long into an abyss, the abyss also gazes into you.",
        ])

    def test_skips_dialogue_long_passages_and_non_latin(self):
        text = "\n".join([
            "== Quotes ==",
            "* '''Paul''': I must not fear, said the boy to his mother quietly.",
            "* Jessica: We have come to a new world and must learn its ways.",
            "* " + " ".join(["word"] * 40) + ".",
            "* 猿も木から落ちる、とはよく言ったものです。本当に。",
            "* Fear is the mind-killer.",
        ])
        self.assertEqual(sources.parse_wikiquote(text), ["Fear is the mind-killer."])

    def test_proverb_pages_keep_only_the_bold_proverb(self):
        text = "\n".join([
            "== A ==",
            "* '''A rolling stone gathers no moss.''' Meaning: people who keep moving avoid responsibility.",
            "** Recorded in 1546.",
            "* Actions speak louder than words.",
            "* Anger makes dull men witty, but it keeps them poor. (Francis Bacon)",
            "* A good beginning makes a good ending. (14th century)",
        ])
        self.assertEqual(sources.parse_wikiquote(text, proverbs=True), [
            "A rolling stone gathers no moss.",
            "Actions speak louder than words.",
            "A good beginning makes a good ending.",
        ])


    def test_uses_english_translation_of_original_language_quotes(self):
        text = "\n".join([
            "== Quotes ==",
            "* ''Freier Wille ohne Fatum ist ebenso wenig denkbar, wie Geist ohne Reelles''.",
            "** Free will without fate is no more conceivable than spirit without matter.",
            "** \"Fatum und Geschichte,\" April 1862",
            "* ''La culture est un instrument manié par des professeurs.''[http://x.org/a]",
            "** Translation: Culture is an instrument wielded by professors.[http://x.org/b]",
            "* ''Nur wer sich wandelt, bleibt mit mir verwandt und wandelt weiter.''",
            "** ''Jenseits von Gut und Böse'' (1886)",
            "* ''What does not kill me makes me stronger, it is said.''",
            "* Il ne faut pas s’offenser que les autres nous cachent la vérité.",
            "** We should not be upset that others hide the truth from us.",
            "** Maxim 11 from the ''Manuscrit de Liancourt''.",
            "* ''Und wenn du lange in einen Abgrund blickst, blickt der Abgrund auch in dich hinein.''",
            "** '''And when you gaze long into an abyss the abyss also gazes into you.'''",
            "** Chapter IV, aphorism 146, tr. R. J. Hollingdale",
        ])
        self.assertEqual(sources.parse_wikiquote(text), [
            "Free will without fate is no more conceivable than spirit without matter.",
            "Culture is an instrument wielded by professors.",
            "What does not kill me makes me stronger, it is said.",
            "We should not be upset that others hide the truth from us.",
            "And when you gaze long into an abyss the abyss also gazes into you.",
        ])

    def test_section_limits_to_one_level3_heading(self):
        text = "\n".join([
            "== Quotes ==",
            "=== ''[[w:Looking for Alaska|Looking for Alaska]]'' (2005) ===",
            "* Imagining the future is a kind of nostalgia.",
            "=== ''Paper Towns'' (2008) ===",
            "* What a treacherous thing to believe that a person is more than a person.",
        ])
        self.assertEqual(sources.parse_wikiquote(text, section="Looking for Alaska"),
                         ["Imagining the future is a kind of nostalgia."])
        self.assertEqual(len(sources.parse_wikiquote(text)), 2)

    def test_film_pages(self):
        text = "\n".join([
            "== Yubaba ==",
            "* So, your name's Chihiro? [uses magic to steal the kanji] And it belongs to me now.",
            "== San/Princess Mononoke ==",
            "* \"Cut off a wolf's head and it still has the power to bite.\"",
            "== Others ==",
            "* Time to go.  Nobody in particular said this, at some length.",
            "== Other ==",
            "* '''Agent Halpern''': We're a world with no single leader.",
            "== Dialogue ==",
            ":'''Col. Weber''': How would you approach translating this?",
            ":'''Dr. Louise Banks''': I do not know.",
            "<hr width=\"50%\"/>",
            ":'''Roy Batty''': All those moments will be lost in time, like tears in rain.",
            "",
            "== Cast ==",
            "* Rutger Hauer as Roy Batty, the replicant leader",
        ])
        self.assertEqual(sources.parse_quotes(text, film=True), [
            ("So, your name's Chihiro? And it belongs to me now.", "Yubaba"),
            ("Cut off a wolf's head and it still has the power to bite.", "San"),
            ("Time to go. Nobody in particular said this, at some length.", None),
            ("We're a world with no single leader.", "Agent Halpern"),
            ("All those moments will be lost in time, like tears in rain.", "Roy Batty"),
        ])

    def test_film_attribution(self):
        page = "== Tyler Durden ==\n* The things you own end up owning you.\n== Others ==\n* Nobody is credited for this line here."
        film = {"author": "Fight Club", "wikiquote": "Fight Club (film)", "kind": "film"}
        with unittest.mock.patch.object(sources, "fetch_wikitext", return_value=page):
            got = [(c["author"], c["work"]) for c in sources.wikiquote_candidates(film)]
        self.assertEqual(got, [("Tyler Durden", "Fight Club"), ("Fight Club", None)])

    def test_skips_narrated_dialogue(self):
        text = "\n".join([
            "== Quotes ==",
            "* \"Dwarfs and trolls get along like a house on fire,\" said Nobby. \"Ever been in one?\"",
            "* “It’s not that simple,” asked Granny, looking up from the fire.",
            "* People aren’t just people; they are people surrounded by circumstances.",
        ])
        self.assertEqual(sources.parse_wikiquote(text), [
            "People aren’t just people; they are people surrounded by circumstances.",
        ])

    def test_strips_trailing_dates_and_translator_notes(self):
        text = "\n".join([
            "== Quotes ==",
            "* The absent are always in the wrong. (1640)",
            "* To be free of passion and yet full of love. (Hays translation)",
            "* A good beginning makes a good ending. (14th century)",
            "* Fine feathers make fine birds. (Simpson , 2009)",
            "* Kings (and queens) are only human, after all.",
        ])
        self.assertEqual(sources.parse_wikiquote(text), [
            "The absent are always in the wrong.",
            "To be free of passion and yet full of love.",
            "A good beginning makes a good ending.",
            "Fine feathers make fine birds.",
            "Kings (and queens) are only human, after all.",
        ])


class EntryIds(unittest.TestCase):
    start, every = dt.timedelta(hours=11, minutes=45), dt.timedelta(hours=6)

    def eid(self, *when, **kw):
        return entry_id(dt.datetime(*when), start=self.start, every=self.every, **kw)

    def test_slot_times(self):
        self.assertEqual(slot_times(self.start, self.every), ["0545", "1145", "1745", "2345"])

    def test_id_is_the_slot_start_even_when_late(self):
        self.assertEqual(self.eid(2026, 10, 20, 11, 45), "2026-10-20-1145")
        self.assertEqual(self.eid(2026, 10, 20, 12, 30), "2026-10-20-1145")
        self.assertEqual(self.eid(2026, 10, 20, 17, 44), "2026-10-20-1145")
        self.assertEqual(self.eid(2026, 10, 20, 17, 45), "2026-10-20-1745")
        self.assertEqual(self.eid(2026, 10, 20, 23, 50), "2026-10-20-2345")
        self.assertEqual(self.eid(2026, 10, 21, 0, 40), "2026-10-20-2345")  # late, past midnight
        self.assertEqual(self.eid(2026, 10, 21, 6, 0), "2026-10-21-0545")

    def test_explicit_date_and_slot(self):
        self.assertEqual(self.eid(2026, 10, 20, 13, 7, date="2026-10-25"), "2026-10-25-0545")
        self.assertEqual(self.eid(2026, 10, 20, 13, 7, date="2026-10-25", slot="1745"), "2026-10-25-1745")


class Schedule(unittest.TestCase):
    start, every = dt.timedelta(hours=11, minutes=45), dt.timedelta(hours=6)
    nz = ZoneInfo("Pacific/Auckland")
    NZDT, NZST = "45 4,10,16,22 * * *", "45 5,11,17,23 * * *"

    def check(self, cron, *utc):
        return scheduled_for_now(cron, dt.datetime(*utc, tzinfo=dt.timezone.utc), self.nz, self.start, self.every)

    def test_daylight_time_uses_the_utc13_cron(self):  # October: NZDT
        self.assertTrue(self.check(self.NZDT, 2026, 10, 20, 22, 50))   # 11:45 local
        self.assertTrue(self.check(self.NZDT, 2026, 10, 20, 5, 20))    # 17:45, late start
        self.assertTrue(self.check(self.NZDT, 2026, 10, 20, 16, 46))   # 05:45
        self.assertFalse(self.check(self.NZST, 2026, 10, 20, 23, 50))  # 12:45 local

    def test_standard_time_uses_the_utc12_cron(self):  # July: NZST
        self.assertTrue(self.check(self.NZST, 2026, 7, 14, 23, 50))    # 11:45 local
        self.assertTrue(self.check(self.NZST, 2026, 7, 14, 17, 46))    # 05:45
        self.assertFalse(self.check(self.NZDT, 2026, 7, 14, 22, 50))   # 10:45 local


class Extraction(unittest.TestCase):
    def test_json_in_fences(self):
        self.assertEqual(extract_json('```json\n{"index": 3}\n```')["index"], 3)

    def test_svg_in_chatter(self):
        self.assertTrue(extract_svg("Here:\n<svg viewBox='0 0 1 1'><g/></svg>\nDone").startswith("<svg"))


class Sanitiser(unittest.TestCase):
    def lines(self, n=15):
        return "".join(f'<line x1="{i}" y1="0" x2="{i}" y2="800" stroke="#ff0000"/>' for i in range(n))

    def test_strips_dangerous_content_and_forces_grey(self):
        raw = (f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" onload="alert(1)">'
               f'<rect width="800" height="800" fill="#ffffff"/>'
               f'<script>alert(1)</script><foreignObject><div/></foreignObject>'
               f'<image href="http://evil/x.png"/>{self.lines()}</svg>')
        out = image.sanitize_svg(raw)
        for bad in ("script", "onload", "foreignObject", "image", "evil", "red", "ff0000"):
            self.assertNotIn(bad, out)
        self.assertIn('viewBox="0 0 800.0 800.0"', out)
        self.assertIn('fill="#000000"', out)       # white background flipped to black
        self.assertIn('stroke="#808080"', out)     # dark red → lifted to visible grey

    def test_unknown_named_colour_becomes_white(self):
        out = image.sanitize_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">{self.lines()}<circle r="5" stroke="red"/></svg>')
        self.assertIn('<circle r="5" stroke="#ffffff"', out)

    def test_adds_background_when_missing(self):
        out = image.sanitize_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">{self.lines()}</svg>')
        self.assertIn('fill="#000000"', out)

    def test_rejects_sparse_and_invalid(self):
        with self.assertRaises(image.SVGRejected):
            image.sanitize_svg('<svg xmlns="http://www.w3.org/2000/svg"><line/></svg>')
        with self.assertRaises(image.SVGRejected):
            image.sanitize_svg('<svg><unclosed></svg>')
        with self.assertRaises(image.SVGRejected):
            image.sanitize_svg('<!DOCTYPE x [<!ENTITY a "b">]><svg/>')

    def test_fallback_is_valid(self):
        self.assertTrue(image.sanitize_svg(image.fallback_svg("seed")))



class Config(unittest.TestCase):
    """seeds.toml and baked_quotes.json agree on sentiment names."""

    def setUp(self):
        self.cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        self.names = {s["name"] for s in self.cfg["sentiments"]}

    def test_every_source_has_known_sentiments(self):
        for src in self.cfg["sources"]:
            self.assertTrue(src.get("sentiments"), f"{src['wikiquote']} has no sentiments")
            self.assertLessEqual(set(src["sentiments"]), self.names, src["wikiquote"])

    def test_every_sentiment_has_a_source(self):
        tagged = {n for src in self.cfg["sources"] for n in src["sentiments"]}
        self.assertEqual(self.names - tagged, set())

    def test_baked_quotes_use_known_sentiments(self):
        for q in json.loads(BAKED.read_text(encoding="utf-8")):
            self.assertLessEqual(set(q["sentiments"]), self.names, q["text"][:40])

if __name__ == "__main__":
    unittest.main()
