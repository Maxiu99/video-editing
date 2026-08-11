"""Turn an aligned script into a ready-to-render EDL.

Every kept script line becomes one clip, butt-joined to the next, which is
what removes the dead air between sentences. Caption times are computed
against the *output* timeline, so dropping a line renumbers everything
downstream automatically.

    python3 tools/make_edl.py aligned.json -o cut.edl.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reels import captions as captions_mod  # noqa: E402

# Line numbers below are 1-based, matching tools/align_script.py output.

# Cut for time. 9-12 elaborate a point line 7-8 already makes; 33 repeats
# the qualifier the hook opened with; 6 is instructional filler.
DROP = {6, 9, 10, 11, 12, 33}

# The speaker lingers on the sign-off; trim the tail rather than the words.
TRIM_END = {38: 1.6}

# Where the graphic treatment goes. Everything unlisted is a body caption.
STYLES = {
    5:  "hook",           # the hook question
    13: "statement",      # beat change
    26: "card_accent",    # credibility
    30: "card_accent",    # the emotional payoff
    36: "card_accent",    # the offer
    37: "card_accent",    # the click instruction
}

# The four-question checklist, stacked on screen as it is spoken. Each entry
# is (first line, last line, card text) -- 24 and 25 are one question.
STACK = [
    (20, 20, "这一餐主食是什么？"),
    (21, 21, "蔬菜怎么搭？"),
    (22, 22, "蛋白质怎么安排？"),
    (23, 23, "用什么方式煮？"),
    (24, 25, "身体给你什么反馈？"),
]

# The stack sits on her torso: clear of the chin above and of the
# caption/CTA strip below.
STACK_BASE_MARGIN = 470      # bottom card, distance from frame bottom
STACK_STEP = 100             # row pitch

CLIP_HOLD = 0.08             # breath left on the end of each clip
SPEED = 1.05                 # imperceptible on speech, buys ~4s
FPS = 30
TARGET = 88.0                # leave headroom under the 90s Reels ceiling


def quantised(src_dur: float, speed: float, fps: int = FPS) -> float:
    """Clip length as the encoder will actually emit it.

    Each segment is encoded on its own, so a partial trailing frame is
    rounded up to a whole one. Across 30-odd clips that rounding is worth
    about a second -- enough to push a 89s cut over the 90s limit.
    """
    return math.ceil(src_dur / speed * fps) / fps


def build(aligned: list[dict], speed: float) -> dict:
    for i, line in enumerate(aligned, start=1):
        line["i"] = i

    kept = [l for l in aligned if l["i"] not in DROP]

    # Extend each clip into the pause that follows, but never past the next
    # kept line -- otherwise a word from the next sentence leaks in.
    for pos, line in enumerate(kept):
        end = line["end"] - TRIM_END.get(line["i"], 0.0)
        if pos + 1 < len(kept):
            room = kept[pos + 1]["start"] - end
            end += max(0.0, min(CLIP_HOLD, room))
        line["clip_in"] = line["start"]
        line["clip_out"] = end

    clips, captions = [], []
    cursor = 0.0
    starts: dict[int, float] = {}   # line number -> output-timeline start
    ends: dict[int, float] = {}

    for line in kept:
        src_dur = line["clip_out"] - line["clip_in"]
        out_dur = quantised(src_dur, speed)
        clips.append({
            "in": round(line["clip_in"], 3),
            "out": round(line["clip_out"], 3),
            "speed": speed,
            "comment": f"{line['i']:02d} {line['text']}",
        })
        starts[line["i"]] = cursor
        ends[line["i"]] = cursor + out_dur
        cursor += out_dur

    stacked = {n for first, last, _ in STACK for n in range(first, last + 1)}
    stack_end = max(ends[last] for _, last, _ in STACK if last in ends)

    for line in kept:
        n = line["i"]
        if n in stacked:
            continue        # the stack below speaks for these
        start, end = starts[n], ends[n]
        captions.append({
            "t": round(start + 0.04, 2),
            "d": round(max(0.4, end - start - 0.04), 2),
            "text": line["text"].rstrip("，。：；"),
            "style": STYLES.get(n, "body"),
        })

    # Each checklist card enters on its line and stays until the list ends,
    # so the questions accumulate instead of replacing one another. Rows are
    # spaced by the height each card actually wraps to -- a fixed pitch lets
    # a two-line card overlap the one above it.
    size = captions_mod.PRESETS["card"]["size"]
    pad = captions_mod.PRESETS["card"]["pad"]
    heights = []
    for _, _, text in STACK:
        lines = captions_mod.wrap_text(text, size, 1080, 90 + pad).count(r"\N")
        heights.append((lines + 1) * size * 1.25 + pad)

    margin = STACK_BASE_MARGIN
    margins = []
    for height in reversed(heights):        # lay out bottom row upwards
        margins.append(margin)
        margin += max(STACK_STEP, height)
    margins.reverse()

    for row, (first, last, text) in enumerate(STACK):
        if first not in starts:
            continue
        captions.append({
            "t": round(starts[first] + 0.04, 2),
            "d": round(stack_end - starts[first] + 0.35, 2),
            "text": text,
            "style": "card",
            "margin_v": int(margins[row]),
        })

    captions.sort(key=lambda c: c["t"])

    return {
        "title": "reel_90",
        "source": "raw/A1.MOV",
        "grade": "warm",
        "preset": "medium",
        "crf": 20,
        "output": {"width": 1080, "height": 1920, "fps": 30},
        "clips": clips,
        "captions": captions,
        "audio": {"lufs": -14},
        "brand": {"progress_bar": True, "progress_color": "#FFD400"},
        "_runtime_estimate": round(cursor, 2),
    }


def solve_speed(aligned: list[dict], target: float,
                low: float = 1.0, high: float = 1.4) -> float:
    """Smallest speed-up that brings the runtime to ``target``."""
    if build(aligned, low)["_runtime_estimate"] <= target:
        return low
    for _ in range(40):
        mid = (low + high) / 2
        if build(aligned, mid)["_runtime_estimate"] > target:
            low = mid
        else:
            high = mid
    return round(high, 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("aligned", nargs="?", default="aligned.json")
    ap.add_argument("-o", "--out", default="cut.edl.json")
    ap.add_argument("--speed", type=float, default=SPEED)
    ap.add_argument("--target", type=float, default=None,
                    help=f"solve for the speed that lands here (try {TARGET})")
    args = ap.parse_args()

    with open(args.aligned) as fh:
        aligned = json.load(fh)

    speed = args.speed
    if args.target:
        speed = solve_speed(aligned, args.target)
        print(f"speed {speed:.3f}x to land at {args.target:.0f}s")
    edl = build(aligned, speed)
    with open(args.out, "w") as fh:
        json.dump(edl, fh, ensure_ascii=False, indent=2)

    print(f"{len(edl['clips'])} clips, {len(edl['captions'])} captions")
    print(f"estimated runtime {edl['_runtime_estimate']:.1f}s at {speed}x")
    if edl["_runtime_estimate"] > 90:
        print("! over the 90s Reels limit -- drop another line or raise speed")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
