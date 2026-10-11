"""Ask the model to choose one real candidate and write a visual brief.

The model picks by index, so the stored quote text is always copied verbatim
from a real source — the model never writes or edits the quote itself.
"""

from __future__ import annotations

import random

from .llm import LLM, extract_json

CURATOR_SYSTEM = """You curate one aphorism per day for a minimal website.
You will be given a mood and a numbered list of real lines (quotes, maxims or
proverbs). Choose the single line that is the best aphorism for the mood:
- states a general truth or insight, not a description of a particular event,
  character or plot moment;
- makes complete sense on its own, with no surrounding context or names needed;
- is concise and memorable — ideally one sentence, under about 30 words, though
  a short passage of a few lines (a litany, a verse, a book's key lines) is
  welcome when it stands on its own;
- rewards a second reading.
Reject dialogue, narration, lists, and lines that only make sense in the source
(a short two-line exchange is fine if, together, it stands on its own).
Lines of a passage are separated by " / " in the list.
If none fit well, choose the closest one anyway.

Then write a brief for a white-on-black line drawing that captures the quote's
feeling indirectly, by metaphor rather than illustration. The subject can be an
object, machine, structure, instrument or natural form, or a scene or situation
with the right sentiment: a quote about loneliness might be a lone probe
travelling past the outer planets; one about persistence, a lighthouse in a
storm. Choose the style that suits it: a precise engineering or architectural
drawing (orthographic views, sections, dimensions), or a looser industrial
design sketch (confident perspective linework, construction lines, callouts).
No faces or portraits, and no literal text. One or two sentences, concrete and
drawable, naming the style. Do not use the quote's words as labels.

Reply with JSON only:
{"index": <number>, "visual_brief": "<brief>", "alt": "<5-12 word image description>"}"""


def curate(llm: LLM, sentiment: dict, candidates: list[dict], rng: random.Random, limit: int = 40) -> dict:
    pool = candidates[:]
    rng.shuffle(pool)
    pool = pool[:limit]
    listing = "\n".join(f"{i}. {' / '.join(c['text'].splitlines())}" for i, c in enumerate(pool))
    user = f"Mood: {sentiment['name']} — {sentiment['description']}\n\nQuotes:\n{listing}"

    for _ in range(2):
        try:
            reply = extract_json(llm.chat(CURATOR_SYSTEM, user, temperature=0.4))
            idx = int(reply["index"])
            brief = str(reply["visual_brief"]).strip()
            if 0 <= idx < len(pool) and brief:
                return {**pool[idx], "visual_brief": brief, "alt": str(reply.get("alt") or brief)[:160]}
        except Exception as e:  # noqa: BLE001
            print(f"  curator: bad reply ({e}), retrying")
    print("  curator: falling back to a random candidate")
    pick = rng.choice(pool)
    return {**pick, "visual_brief": f"An abstract precision instrument evoking: {sentiment['name'].lower()}, as an engineering drawing",
            "alt": "Technical line drawing of a precision instrument"}
