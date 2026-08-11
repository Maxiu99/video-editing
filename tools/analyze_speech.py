"""Find the speech segments in a clip and report what tightening buys you.

Talking-head rushes are mostly dead air. This runs ffmpeg's silencedetect,
turns the gaps into a list of speech spans, and reports how much runtime
comes back if every pause is capped at a given length -- which is usually
the difference between a 135s take and a 90s reel.

    python3 tools/analyze_speech.py raw/A1.MOV --max-pause 0.28
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reels.ffmpeg_util import ffmpeg_bin, probe  # noqa: E402

START_RE = re.compile(r"silence_start: (-?\d+\.?\d*)")
END_RE = re.compile(r"silence_end: (-?\d+\.?\d*)")


def detect_silences(path: str, noise_db: float,
                    min_silence: float) -> list[tuple[float, float]]:
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", path, "-map", "0:a",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts, ends = [], []
    for line in proc.stderr.splitlines():
        m = START_RE.search(line)
        if m:
            starts.append(float(m.group(1)))
        m = END_RE.search(line)
        if m:
            ends.append(float(m.group(1)))
    # A silence still open at EOF has no matching end.
    if len(starts) > len(ends):
        ends.append(probe(path).duration)
    return list(zip(starts, ends))


def speech_spans(silences: list[tuple[float, float]], duration: float,
                 pad: float = 0.12) -> list[tuple[float, float]]:
    """Invert the silence list, padding each span so words are not clipped."""
    spans, cursor = [], 0.0
    for start, end in silences:
        if start > cursor:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        spans.append((cursor, duration))

    padded = []
    for start, end in spans:
        padded.append((max(0.0, start - pad), min(duration, end + pad)))

    # Padding can make neighbours touch; fuse anything that now overlaps.
    merged = [padded[0]] if padded else []
    for start, end in padded[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [(s, e) for s, e in merged if e - s > 0.12]


def tighten(spans: list[tuple[float, float]],
            max_pause: float) -> tuple[float, list[float]]:
    """Runtime if every inter-span gap is capped at ``max_pause``."""
    if not spans:
        return 0.0, []
    total = sum(e - s for s, e in spans)
    gaps = [max(0.0, min(max_pause, spans[i][0] - spans[i - 1][1]))
            for i in range(1, len(spans))]
    return total + sum(gaps), gaps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--noise-db", type=float, default=-32.0)
    ap.add_argument("--min-silence", type=float, default=0.28)
    ap.add_argument("--pad", type=float, default=0.12)
    ap.add_argument("--max-pause", type=float, default=0.25)
    ap.add_argument("--json", help="write the speech spans here")
    args = ap.parse_args()

    duration = probe(args.video).duration
    silences = detect_silences(args.video, args.noise_db, args.min_silence)
    spans = speech_spans(silences, duration, args.pad)
    speech = sum(e - s for s, e in spans)
    tightened, _ = tighten(spans, args.max_pause)

    print(f"source          {duration:8.2f}s")
    print(f"speech          {speech:8.2f}s  in {len(spans)} spans")
    print(f"dead air        {duration - speech:8.2f}s  "
          f"({(duration - speech) / duration * 100:.0f}%)")
    print(f"pauses <= {args.max_pause:.2f}s {tightened:8.2f}s  "
          f"<- runtime after tightening")
    print()
    print(f"{'#':>3}  {'start':>7}  {'end':>7}  {'len':>6}  {'gap before':>10}")
    prev_end = 0.0
    for i, (start, end) in enumerate(spans):
        print(f"{i:3d}  {start:7.2f}  {end:7.2f}  {end - start:6.2f}  "
              f"{start - prev_end:10.2f}")
        prev_end = end

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"duration": duration,
                       "spans": [{"start": s, "end": e} for s, e in spans]},
                      fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
