import unittest

import datetime as dt

from generator import image, sources
from generator.__main__ import entry_id
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
    def test_slot_follows_the_local_hour(self):
        self.assertEqual(entry_id(dt.datetime(2026, 10, 12, 1, 7)), "2026-10-12-am")
        self.assertEqual(entry_id(dt.datetime(2026, 10, 12, 13, 7)), "2026-10-12-pm")

    def test_explicit_date_and_slot(self):
        self.assertEqual(entry_id(dt.datetime(2026, 10, 12, 13, 7), "2026-10-20"), "2026-10-20-am")
        self.assertEqual(entry_id(dt.datetime(2026, 10, 12, 1, 7), "2026-10-20", "pm"), "2026-10-20-pm")
        self.assertEqual(entry_id(dt.datetime(2026, 10, 12, 1, 7), slot="pm"), "2026-10-12-pm")


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


if __name__ == "__main__":
    unittest.main()
