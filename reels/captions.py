"""Animated burn-in captions, generated as an ASS subtitle file.

libass gives us per-word karaoke highlighting, pop-in scaling and thick
outlines -- the caption vocabulary Reels actually uses -- which the
`drawtext` filter cannot express.
"""

from __future__ import annotations

import re

# ASS colours are &HAABBGGRR: alpha, then blue/green/red.
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"
YELLOW = "&H0000D7FF"
GREEN = "&H0044E37C"
SHADOW = "&H80000000"

CJK = re.compile(r"[　-鿿豈-﫿＀-￯]")

LATIN_FONT = "DejaVu Sans"
CJK_FONT = "WenQuanYi Zen Hei"

# Facebook overlays its own UI on the reel. Keeping text inside these
# bounds stops captions from landing under the caption/CTA strip.
SAFE_TOP = 0.11
SAFE_BOTTOM = 0.20

PRESETS: dict[str, dict] = {
    # The opening line: large, high on the frame, impossible to scroll past.
    "hook": {
        "size": 104, "primary": YELLOW, "outline": 9, "align": 8,
        "margin_v": 300, "anim": "pop", "upper": True,
    },
    # Standard narration captions.
    "body": {
        "size": 78, "primary": WHITE, "outline": 7, "align": 2,
        "margin_v": 620, "anim": "pop", "upper": False,
    },
    # Word-by-word highlight, timed evenly across the line.
    "karaoke": {
        "size": 80, "primary": YELLOW, "secondary": WHITE, "outline": 7,
        "align": 2, "margin_v": 620, "anim": "none", "upper": False,
    },
    # A quiet label -- product name, location, source.
    "label": {
        "size": 46, "primary": WHITE, "outline": 4, "align": 2,
        "margin_v": 480, "anim": "fade", "upper": False,
    },
    # Full-frame statement card, for beat changes.
    "statement": {
        "size": 118, "primary": WHITE, "outline": 10, "align": 5,
        "margin_v": 0, "anim": "pop", "upper": True,
    },
    # Motion-graphic card: text on an opaque slab rather than an outline.
    # Cheaper and sharper than cutting to b-roll, and it keeps the speaker
    # on screen.
    "card": {
        "size": 64, "primary": WHITE, "box": "&H00232323", "pad": 18,
        "align": 2, "margin_v": 900, "anim": "slide", "upper": False,
    },
    # The same slab in the accent colour, for the one line that matters most.
    "card_accent": {
        "size": 66, "primary": "&H00101010", "box": YELLOW, "pad": 20,
        "align": 2, "margin_v": 900, "anim": "pop", "upper": False,
    },
}

ANIMATIONS = {
    "pop": r"{\fad(70,70)\fscx62\fscy62\t(0,130,\fscx106\fscy106)"
           r"\t(130,230,\fscx100\fscy100)}",
    "fade": r"{\fad(180,180)}",
    "slide": r"{\fad(80,80)\move(540,%(y_from)d,540,%(y_to)d,0,220)}",
    "none": "",
}


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def pick_font(text: str) -> str:
    return CJK_FONT if CJK.search(text) else LATIN_FONT


def _style_name(index: int) -> str:
    return f"S{index}"


def _split_words(text: str) -> list[str]:
    """Split for karaoke: CJK highlights per character, Latin per word."""
    if CJK.search(text):
        return [c for c in text if not c.isspace()]
    return text.split()


def _karaoke_body(text: str, duration: float) -> str:
    words = _split_words(text)
    if not words:
        return text
    # \k units are centiseconds; distribute the line evenly and give the
    # remainder to the last word so the highlight ends exactly on time.
    total_cs = max(1, int(round(duration * 100)))
    per = max(1, total_cs // len(words))
    joiner = "" if CJK.search(text) else " "
    parts = []
    for i, word in enumerate(words):
        cs = total_cs - per * (len(words) - 1) if i == len(words) - 1 else per
        parts.append(rf"{{\k{max(1, cs)}}}{word}")
    return joiner.join(parts)


def _escape(text: str) -> str:
    """Neutralise ASS override braces.

    Backslashes are left alone on purpose so caption text can use the ASS
    line break ``\\N`` directly.
    """
    return text.replace("{", "(").replace("}", ")")


def build_ass(captions: list[dict], width: int = 1080, height: int = 1920,
              default_style: str = "body") -> str:
    """Render a caption list into a complete ASS document.

    Each caption is ``{"t": start, "d": duration, "text": str,
    "style": preset name, ...}``; any preset key may be overridden inline.
    """
    styles: list[str] = []
    events: list[str] = []

    for i, cap in enumerate(captions):
        preset = dict(PRESETS.get(cap.get("style", default_style),
                                  PRESETS[default_style]))
        # Inline overrides: size, primary, align, margin_v, anim, upper...
        for key in ("size", "primary", "secondary", "outline", "align",
                    "margin_v", "anim", "upper", "font", "box", "pad"):
            if key in cap:
                preset[key] = cap[key]

        text = str(cap["text"])
        if preset.get("upper"):
            text = text.upper()
        font = preset.get("font") or pick_font(text)
        name = _style_name(i)

        # BorderStyle 3 fills an opaque slab behind the text, and libass
        # paints that slab in OutlineColour with Outline as its padding.
        if preset.get("box"):
            border_style, edge_colour = "3", preset["box"]
            edge_size, shadow_depth = str(preset.get("pad", 18)), "0"
        else:
            border_style, edge_colour = "1", BLACK
            edge_size, shadow_depth = str(preset.get("outline", 6)), "3"

        styles.append(",".join([
            f"Style: {name}",
            font,
            str(preset["size"]),
            preset["primary"],
            preset.get("secondary", WHITE),
            edge_colour,
            SHADOW,                     # back / shadow colour
            "-1",                       # bold
            "0", "0", "0",              # italic, underline, strikeout
            "100", "100",               # scale x/y
            "0", "0",                   # spacing, angle
            border_style,
            edge_size,
            shadow_depth,
            str(preset["align"]),
            "90", "90",                 # margin l/r
            str(preset["margin_v"]),
            "1",                        # encoding
        ]))

        start = float(cap["t"])
        dur = float(cap.get("d", 1.6))
        anim_key = preset.get("anim", "none")
        anim = ANIMATIONS.get(anim_key, "")
        if anim_key == "slide":
            anim = anim % {"y_from": height - preset["margin_v"] + 60,
                           "y_to": height - preset["margin_v"]}

        if cap.get("style") == "karaoke" or preset.get("anim") == "karaoke":
            body = _karaoke_body(_escape(text), dur)
        else:
            body = _escape(text).replace("\n", r"\N")

        # Fields must line up with the Events Format line below:
        # Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        events.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(start + dur)},"
            f"{name},,0,0,0,,{anim}{body}"
        )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    return (header + "\n".join(styles) +
            "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
            "MarginR, MarginV, Effect, Text\n" + "\n".join(events) + "\n")


def check_safe_zone(captions: list[dict], height: int = 1920) -> list[str]:
    """Warn about captions that Facebook's UI chrome would cover."""
    warnings = []
    top_limit = height * SAFE_TOP
    bottom_limit = height * (1 - SAFE_BOTTOM)
    for cap in captions:
        preset = dict(PRESETS.get(cap.get("style", "body"), PRESETS["body"]))
        preset.update({k: cap[k] for k in ("align", "margin_v", "size")
                       if k in cap})
        align, margin, size = preset["align"], preset["margin_v"], preset["size"]
        if align in (7, 8, 9):
            y = margin
            if y < top_limit:
                warnings.append(
                    f'caption "{cap["text"][:24]}" sits {int(top_limit - y)}px '
                    "above the safe zone (top UI may cover it)")
        elif align in (1, 2, 3):
            y = height - margin
            if y > bottom_limit:
                warnings.append(
                    f'caption "{cap["text"][:24]}" sits '
                    f"{int(y - bottom_limit)}px below the safe zone "
                    "(caption/CTA strip may cover it)")
    return warnings
