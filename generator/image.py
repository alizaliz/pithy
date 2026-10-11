"""White-on-black line art (technical drawings, design sketches) as SVG: prompt, sanitiser and fallback."""

from __future__ import annotations

import math
import random
import re
import xml.etree.ElementTree as ET

from .llm import LLM, extract_svg

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
VIEWBOX = (0, 0, 800, 800)

SVG_SYSTEM_PROMPT = """You are a draughtsman and industrial designer producing line drawings as SVG code.

Draw in whichever of these styles the brief asks for (default to the one that suits it):
- Technical drawing: like an engineering or architectural drawing. Orthographic or
  isometric views, construction lines, section hatching, centre lines (dash-dot),
  dimension lines with small arrowheads, a thin border frame.
- Industrial design sketch: like a designer's marker-and-fineliner concept sketch.
  Confident perspective linework, construction and guide lines that overshoot their
  corners, a few lighter repeated strokes, hatching for shadow, ellipse guides,
  small callout arrows or detail views. Loose but accurate; no border frame needed.
Subjects can be objects, machines and structures, or scenes: a spacecraft on its
trajectory past planets, a bridge across a gorge, a vessel at sea. Render scenes in
the same line style, e.g. orbits as dashed ellipses, terrain as contour lines.

Style rules (strict):
- White lines on a pure black background. Use only #ffffff, light greys, or black.
- Line art only: stroke-based drawing, fill="none" except for tiny arrowheads or dots.
- Stroke widths between 0.5 and 2.5. Vary weight: outlines heavier, detail and
  construction lines lighter (stroke-opacity may be used for faint guide lines).
- At most 3 short labels (1–3 words, uppercase), font-family="monospace", font-size 12–16,
  fill="#ffffff". Labels are optional; keep them clear of other lines.
- Rich detail: aim for 40–150 drawing elements.
- viewBox="0 0 800 800". Begin with <rect width="800" height="800" fill="#000000"/>.
- No <script>, <image>, <foreignObject>, <use>, CSS <style>, filters, gradients,
  external references or event handlers.

Reply with the SVG code only, nothing else."""

DRAW_TAGS = {"path", "line", "polyline", "polygon", "circle", "ellipse", "rect", "text"}
ALLOWED_TAGS = DRAW_TAGS | {"svg", "g", "tspan", "defs", "marker", "clipPath", "pattern", "title"}
ALLOWED_ATTRS = {
    "viewBox", "xmlns", "id", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "width", "height", "d", "points", "transform", "fill", "stroke", "stroke-width",
    "stroke-dasharray", "stroke-dashoffset", "stroke-linecap", "stroke-linejoin",
    "stroke-miterlimit", "opacity", "stroke-opacity", "fill-opacity", "font-size",
    "font-family", "font-weight", "text-anchor", "dominant-baseline", "letter-spacing",
    "markerWidth", "markerHeight", "refX", "refY", "orient", "markerUnits", "marker-start",
    "marker-mid", "marker-end", "clip-path", "patternUnits", "patternTransform",
    "preserveAspectRatio", "dx", "dy",
}
URL_REF = re.compile(r"^url\(#[\w-]+\)$")
NAMED = {"white": "#ffffff", "black": "#000000", "gray": "#808080", "grey": "#808080",
         "silver": "#c0c0c0", "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3",
         "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "dimgray": "#696969", "currentcolor": "#ffffff"}


class SVGRejected(ValueError):
    pass


def _to_grey(value: str, default: str) -> str:
    v = value.strip().lower()
    if v in ("none", "transparent") or URL_REF.match(v):
        return v
    v = NAMED.get(v, v)
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    else:
        m = re.fullmatch(r"rgba?\((\d+)[, ]+(\d+)[, ]+(\d+).*\)", v)
        if not m:
            return default
        r, g, b = (min(255, int(x)) for x in m.groups())
    lum = round(0.2126 * r + 0.7152 * g + 0.0722 * b)
    return f"#{lum:02x}{lum:02x}{lum:02x}"


def _visible_stroke(grey: str) -> str:
    """Dim coloured strokes vanish on black: lift mid/dark greys, keep true black."""
    m = re.fullmatch(r"#([0-9a-f]{2})\1\1", grey)
    if not m:
        return grey
    lum = int(m.group(1), 16)
    if 0x18 <= lum < 0x80:
        return "#808080"
    return grey


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def sanitize_svg(raw: str, min_elements: int = 12) -> str:
    """Parse, whitelist, force greyscale + black background. Raises SVGRejected."""
    if len(raw) > 400_000:
        raise SVGRejected("too large")
    if re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
        raise SVGRejected("doctype/entities not allowed")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise SVGRejected(f"invalid XML: {e}") from e
    if _local(root.tag) != "svg":
        raise SVGRejected("root is not <svg>")

    def clean(el: ET.Element) -> None:
        for child in list(el):
            if _local(child.tag) not in ALLOWED_TAGS:
                el.remove(child)
                continue
            child.tag = f"{{{SVG_NS}}}{_local(child.tag)}"
            clean(child)
        for attr in list(el.attrib):
            name = _local(attr)
            if name not in ALLOWED_ATTRS:
                del el.attrib[attr]
                continue
            val = el.attrib.pop(attr)
            if name == "fill":
                val = _to_grey(val, "none")
            elif name == "stroke":
                val = _visible_stroke(_to_grey(val, "#ffffff"))
            elif "url(" in val and not URL_REF.match(val.strip()):
                continue  # drop external references
            el.set(name, val)

    clean(root)
    root.tag = f"{{{SVG_NS}}}svg"
    vb = " ".join(map(str, VIEWBOX))
    if not root.get("viewBox"):
        w, h = root.get("width", "800"), root.get("height", "800")
        try:
            vb = f"0 0 {float(w.rstrip('px'))} {float(h.rstrip('px'))}"
        except ValueError:
            pass
        root.set("viewBox", vb)
    for a in ("width", "height"):
        root.attrib.pop(a, None)

    vx, vy, vw, vh = (float(n) for n in root.get("viewBox").replace(",", " ").split())

    # Any full-canvas rect is the background: force it black.
    def is_full(v: str | None, size: float) -> bool:
        return v is not None and (v.strip() == "100%" or _num(v) >= size * 0.98)

    has_bg = False
    for el in root.iter(f"{{{SVG_NS}}}rect"):
        if is_full(el.get("width"), vw) and is_full(el.get("height"), vh):
            el.set("fill", "#000000")
            has_bg = True
    if not has_bg:
        bg = ET.Element(f"{{{SVG_NS}}}rect", {"x": str(vx), "y": str(vy), "width": str(vw), "height": str(vh), "fill": "#000000"})
        root.insert(0, bg)

    drawn = sum(1 for el in root.iter() if _local(el.tag) in DRAW_TAGS)
    if drawn < min_elements:
        raise SVGRejected(f"too sparse ({drawn} elements)")
    return ET.tostring(root, encoding="unicode")


def _num(v: str) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", v) or 0)
    except ValueError:
        return 0.0


def generate_svg(llm: LLM, brief: str, attempts: int = 3) -> tuple[str, str]:
    """Returns (svg, method). Falls back to a procedural drawing if the model keeps failing."""
    user = f"Draw this:\n{brief}"
    errors = []
    for _ in range(attempts):
        try:
            return sanitize_svg(extract_svg(llm.chat(SVG_SYSTEM_PROMPT, user, temperature=0.9))), "llm"
        except Exception as e:  # noqa: BLE001 — any failure means try again
            errors.append(str(e))
            user = f"Draw this:\n{brief}\n\nPrevious attempt failed: {e}. Follow the rules exactly."
    print(f"  image: falling back to procedural drawing ({'; '.join(errors)})")
    return fallback_svg(brief), "fallback"


def fallback_svg(seed: str) -> str:
    """Deterministic geometric 'instrument' drawing so the page always has an image."""
    rng = random.Random(seed)
    cx = cy = 400
    parts = ['<rect width="800" height="800" fill="#000000"/>',
             '<rect x="40" y="40" width="720" height="720" fill="none" stroke="#ffffff" stroke-width="1.5"/>',
             f'<line x1="{cx}" y1="70" x2="{cx}" y2="730" stroke="#888888" stroke-width="0.75" stroke-dasharray="12 4 2 4"/>',
             f'<line x1="70" y1="{cy}" x2="730" y2="{cy}" stroke="#888888" stroke-width="0.75" stroke-dasharray="12 4 2 4"/>']
    radii = sorted(rng.sample(range(60, 300), 5))
    for i, r in enumerate(radii):
        dash = ' stroke-dasharray="4 6"' if i % 2 else ""
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#ffffff" stroke-width="{2 if i == len(radii) - 1 else 1}"{dash}/>')
    ticks = rng.choice([24, 36, 48, 60])
    for k in range(ticks):
        a = 2 * math.pi * k / ticks
        r1, r2 = radii[-1], radii[-1] + (18 if k % 6 == 0 else 8)
        parts.append(f'<line x1="{cx + r1 * math.cos(a):.1f}" y1="{cy + r1 * math.sin(a):.1f}" '
                     f'x2="{cx + r2 * math.cos(a):.1f}" y2="{cy + r2 * math.sin(a):.1f}" stroke="#ffffff" stroke-width="1"/>')
    n = rng.choice([3, 5, 6, 8])
    pts = " ".join(f"{cx + radii[1] * math.cos(2 * math.pi * k / n):.1f},{cy + radii[1] * math.sin(2 * math.pi * k / n):.1f}" for k in range(n))
    parts.append(f'<polygon points="{pts}" fill="none" stroke="#ffffff" stroke-width="1.25"/>')
    y = 750
    parts.append(f'<line x1="{cx - radii[-1]}" y1="{y - 20}" x2="{cx + radii[-1]}" y2="{y - 20}" stroke="#ffffff" stroke-width="0.75"/>')
    parts.append('<text x="60" y="70" font-family="monospace" font-size="12" fill="#ffffff">FIG. 1</text>')
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 800 800">{"".join(parts)}</svg>'
