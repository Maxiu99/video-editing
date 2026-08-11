"""Locating and driving the ffmpeg/ffprobe binaries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


def _bundled(name: str) -> str | None:
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    if name == "ffmpeg":
        return exe
    # The imageio wheel ships ffmpeg only; look for a sibling ffprobe.
    probe = os.path.join(os.path.dirname(exe), "ffprobe")
    return probe if os.path.exists(probe) else None


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or _bundled("ffmpeg") or _die("ffmpeg")


def ffprobe_bin() -> str | None:
    """ffprobe is optional: probe() falls back to ffmpeg when it is absent."""
    return shutil.which("ffprobe") or _bundled("ffprobe")


def _die(name: str) -> str:
    sys.exit(f"{name} not found. Install it, or `pip install imageio-ffmpeg`.")


def run(args: list[str], quiet: bool = True) -> None:
    """Run ffmpeg, surfacing its stderr tail when it fails."""
    proc = subprocess.run(
        args,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-25:])
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}):\n{tail}")


def ffmpeg(args: list[str], quiet: bool = True) -> None:
    run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args], quiet)


class Probe(dict):
    """Media metadata, with the fields the pipeline actually reads."""

    @property
    def duration(self) -> float:
        return float(self.get("duration") or 0.0)

    @property
    def width(self) -> int:
        return int(self.get("width") or 0)

    @property
    def height(self) -> int:
        return int(self.get("height") or 0)

    @property
    def fps(self) -> float:
        return float(self.get("fps") or 0.0)

    @property
    def has_audio(self) -> bool:
        return bool(self.get("has_audio"))


def probe(path: str) -> Probe:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    pb = ffprobe_bin()
    if pb:
        return _probe_with_ffprobe(pb, path)
    return _probe_with_ffmpeg(path)


def _probe_with_ffprobe(pb: str, path: str) -> Probe:
    out = subprocess.run(
        [pb, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    info = Probe(has_audio=audio is not None)
    info["duration"] = float(data.get("format", {}).get("duration") or 0.0)
    if video:
        info["width"] = video.get("width")
        info["height"] = video.get("height")
        info["fps"] = _parse_rate(video.get("avg_frame_rate")
                                  or video.get("r_frame_rate"))
        if not info["duration"]:
            info["duration"] = float(video.get("duration") or 0.0)
    return info


def _probe_with_ffmpeg(path: str) -> Probe:
    """Parse `ffmpeg -i` stderr when no ffprobe binary is available."""
    import re

    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", path],
                         capture_output=True, text=True)
    text = out.stderr
    info = Probe(has_audio="Audio:" in text)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", text)
    if m:
        h, mn, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mn) * 60 + float(s)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+\.?\d*) fps", text)
    if m:
        info["fps"] = float(m.group(1))
    return info


def _parse_rate(rate: str | None) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)
