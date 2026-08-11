"""Synthesise stand-in footage so the pipeline can be exercised end to end.

Generates a 16:9 "A-roll", two b-roll clips and a music bed with nothing
but lavfi sources -- useful for smoke-testing an EDL before the real
rushes arrive.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reels.ffmpeg_util import ffmpeg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lavfi_clip(out: str, video: str, audio: str, seconds: float) -> None:
    ffmpeg([
        "-f", "lavfi", "-i", video,
        "-f", "lavfi", "-i", audio,
        "-t", f"{seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        out,
    ])
    print(f"  {os.path.relpath(out, ROOT)}")


def main() -> int:
    for sub in ("raw", "broll", "music"):
        os.makedirs(os.path.join(ROOT, sub), exist_ok=True)

    print("generating demo media")

    # A-roll: 1920x1080, so the reframe step has real work to do.
    lavfi_clip(
        os.path.join(ROOT, "raw", "demo_raw.mp4"),
        "testsrc2=size=1920x1080:rate=30",
        # A wobbling tone stands in for dialogue; it drives the ducking.
        "sine=frequency=220:sample_rate=48000:beep_factor=4",
        24,
    )

    lavfi_clip(
        os.path.join(ROOT, "broll", "demo_broll_a.mp4"),
        "mandelbrot=size=1920x1080:rate=30",
        "anullsrc=r=48000:cl=stereo",
        8,
    )

    lavfi_clip(
        os.path.join(ROOT, "broll", "demo_broll_b.mp4"),
        "life=size=1920x1080:rate=30:mold=10:ratio=0.1:death_color=#203040"
        ":life_color=#00ff9c",
        "anullsrc=r=48000:cl=stereo",
        8,
    )

    music = os.path.join(ROOT, "music", "demo_bed.wav")
    ffmpeg([
        "-f", "lavfi", "-i",
        "sine=frequency=110:sample_rate=48000",
        "-f", "lavfi", "-i",
        "sine=frequency=164.81:sample_rate=48000",
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:normalize=0,volume=-8dB,"
        "aformat=channel_layouts=stereo",
        "-t", "40", music,
    ])
    print(f"  {os.path.relpath(music, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
