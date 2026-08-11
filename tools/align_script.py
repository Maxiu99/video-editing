"""Map a known script onto detected speech spans.

Without a transcriber we cannot hear *what* is said, but we do know the
script, and we can measure exactly *when* sound happens. Speaking rate is
near enough constant within one take, so a line's character count predicts
its duration -- which is enough to lay the script over the spans.

Consecutive spans are grouped to consecutive script lines by dynamic
programming, minimising the error between each group's measured length and
the length its character count predicts, with a nudge towards breaking at
the longer pauses (speakers breathe between sentences).

    python3 tools/align_script.py build_speech.json script.txt -o aligned.json

The result is an estimate. It is good enough to choose cuts and to place
captions for review; word-level timing needs a real transcript.
"""

from __future__ import annotations

import argparse
import json
import math
import re

# Characters that carry no speaking time.
PUNCT = re.compile(r"[\s，。、！？：；「」“”\"'（）()·—…,.!?:;]")


def speaking_units(line: str) -> float:
    """Rough syllable count: CJK glyphs are one unit, latin words are two."""
    stripped = PUNCT.sub("", line)
    cjk = sum(1 for c in stripped if "一" <= c <= "鿿")
    latin = len(re.findall(r"[A-Za-z]+", line))
    digits = sum(1 for c in stripped if c.isdigit())
    return cjk + digits + latin * 2


def align(lines: list[str], spans: list[dict],
          gap_bias: float = 0.6) -> list[dict]:
    n, m = len(lines), len(spans)
    if n > m:
        raise SystemExit(
            f"{n} script lines but only {m} speech spans -- merge some lines")

    units = [speaking_units(l) for l in lines]
    total_units = sum(units) or 1.0
    speech_time = sum(s["end"] - s["start"] for s in spans)
    rate = total_units / speech_time  # units per second

    gaps = [0.0] + [spans[i]["start"] - spans[i - 1]["end"]
                    for i in range(1, m)]
    longest_gap = max(gaps) or 1.0

    INF = float("inf")
    # cost[i][j]: best cost for the first i lines consuming the first j spans.
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[-1] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0

    for i in range(1, n + 1):
        expected = units[i - 1] / rate
        # Leave at least one span for each remaining line.
        for j in range(i, m - (n - i) + 1):
            for k in range(i - 1, j):
                if cost[i - 1][k] == INF:
                    continue
                measured = spans[j - 1]["end"] - spans[k]["start"]
                err = (measured - expected) ** 2
                # Breaking where the speaker did not pause is suspicious.
                boundary = gaps[k] if k else longest_gap
                err += gap_bias * math.exp(-boundary / 0.35)
                if cost[i - 1][k] + err < cost[i][j]:
                    cost[i][j] = cost[i - 1][k] + err
                    back[i][j] = k

    if cost[n][m] == INF:
        raise SystemExit("no alignment found")

    out, j = [], m
    for i in range(n, 0, -1):
        k = back[i][j]
        out.append({
            "text": lines[i - 1],
            "start": round(spans[k]["start"], 3),
            "end": round(spans[j - 1]["end"], 3),
            "spans": [k, j - 1],
            "units": units[i - 1],
            "predicted": round(units[i - 1] / rate, 2),
        })
        j = k
    out.reverse()
    for item in out:
        item["measured"] = round(item["end"] - item["start"], 2)
        item["drift"] = round(item["measured"] - item["predicted"], 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spans_json", help="output of analyze_speech.py --json")
    ap.add_argument("script", help="one caption line per row")
    ap.add_argument("-o", "--out", default="aligned.json")
    ap.add_argument("--drop-spans", default="",
                    help="comma-separated span indices to ignore (room noise)")
    ap.add_argument("--gap-bias", type=float, default=0.6)
    args = ap.parse_args()

    with open(args.spans_json) as fh:
        spans = json.load(fh)["spans"]
    drop = {int(x) for x in args.drop_spans.split(",") if x.strip()}
    spans = [s for i, s in enumerate(spans) if i not in drop]

    with open(args.script) as fh:
        lines = [l.strip() for l in fh if l.strip()]

    aligned = align(lines, spans, args.gap_bias)
    with open(args.out, "w") as fh:
        json.dump(aligned, fh, ensure_ascii=False, indent=2)

    print(f"{'start':>7} {'end':>7} {'meas':>6} {'pred':>6} {'drift':>6}  text")
    for item in aligned:
        print(f"{item['start']:7.2f} {item['end']:7.2f} {item['measured']:6.2f} "
              f"{item['predicted']:6.2f} {item['drift']:+6.2f}  {item['text']}")
    worst = max(abs(i["drift"]) for i in aligned)
    print(f"\nworst drift {worst:.2f}s -- treat timings as estimates")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
