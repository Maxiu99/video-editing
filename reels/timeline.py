"""Timeline primitives: time parsing and per-clip video/audio filter chains."""

from __future__ import annotations

import re

TIME_RE = re.compile(r"^(?:(\d+):)?(\d+(?:\.\d+)?)$")


def parse_time(value) -> float:
    """Accept 7, 7.5, "7.5", "0:07.5" or "1:02.25" and return seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    m = TIME_RE.match(text)
    if not m:
        raise ValueError(f"cannot parse time: {value!r}")
    minutes, seconds = m.groups()
    return (int(minutes) * 60 if minutes else 0) + float(seconds)


def reframe_chain(mode: str, w: int, h: int, tag: str = "r") -> str:
    """Fit arbitrary source framing into the vertical canvas.

    crop -- fill the frame and cut the sides (best for talking head)
    blur -- letterbox onto a blurred, zoomed copy of the same frame
    fit  -- letterbox onto solid black

    ``tag`` disambiguates the intermediate pad labels the blur mode needs,
    so several blurred sources can coexist in one filter graph.
    """
    if mode == "crop":
        return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}")
    if mode == "blur":
        return (
            f"split=2[{tag}bgsrc][{tag}fgsrc];"
            f"[{tag}bgsrc]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},gblur=sigma=42,eq=brightness=-0.09[{tag}bg];"
            f"[{tag}fgsrc]scale={w}:{h}:"
            f"force_original_aspect_ratio=decrease[{tag}fg];"
            f"[{tag}bg][{tag}fg]overlay=(W-w)/2:(H-h)/2"
        )
    if mode == "fit":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")
    raise ValueError(f"unknown reframe mode: {mode!r}")


def _even(value: float) -> int:
    return int(round(value / 2.0)) * 2


def zoom_frame_size(amount: float, w: int, h: int) -> tuple[int, int]:
    """Oversampled canvas a zooming clip is reframed onto."""
    return _even(w * (1 + amount)), _even(h * (1 + amount))


def zoom_chain(amount: float, duration: float, w: int, h: int,
               direction: str = "in") -> str:
    """A linear Ken Burns push, expressed as an animated centre crop.

    The clip is reframed onto an oversampled canvas first, then a crop
    window travels from the full canvas down to exactly ``w x h`` (so the
    tight end of the move is pixel-for-pixel sharp) and is scaled back to
    the delivery size.

    ``zoompan`` is the usual filter for this, but with ``d=1`` it fails to
    propagate EOF when an audio stream shares the filter graph and ffmpeg
    spins generating frames forever -- hence the crop formulation.
    """
    sw, sh = zoom_frame_size(amount, w, h)
    progress = rf"min(t/{duration:.4f}\,1)"
    if direction == "out":
        ramp = f"(1+{amount:.4f}*(1-{progress}))"
    else:
        ramp = f"(1+{amount:.4f}*{progress})"
    return (
        f"crop=w='round({sw}/{ramp}/2)*2':h='round({sh}/{ramp}/2)*2'"
        f":x='(iw-ow)/2':y='(ih-oh)/2',"
        f"scale={w}:{h}:flags=bicubic"
    )


GRADES = {
    "none": "",
    # A gentle contrast/saturation lift; reads well on phone screens.
    "punch": "eq=contrast=1.12:saturation=1.18:gamma=0.98",
    # Cooler shadows, lifted blacks.
    "cool": "eq=contrast=1.08:saturation=1.05,"
            "colorbalance=rs=-0.05:bs=0.08:gm=0.02",
    # Warm skin tones for talking-head footage.
    "warm": "eq=contrast=1.08:saturation=1.10,"
            "colorbalance=rs=0.07:bs=-0.05",
    "bw": "hue=s=0,eq=contrast=1.2",
}


def clip_video_chain(clip: dict, w: int, h: int, fps: int,
                     src_duration: float, tag: str = "r") -> str:
    """Full per-clip video chain: reframe -> zoom -> grade -> speed -> fps.

    ``src_duration`` is the length of the *source* span being used. The
    zoom ramp is keyed off it because the crop filter reads source
    timestamps -- the speed change is applied further down the chain.
    """
    zoom = float(clip.get("zoom", 0) or 0)
    if zoom:
        # Reframe onto an oversampled canvas so the push has room to travel.
        sw, sh = zoom_frame_size(abs(zoom), w, h)
        parts = [reframe_chain(clip.get("reframe", "crop"), sw, sh, tag),
                 zoom_chain(abs(zoom), src_duration, w, h,
                            "out" if zoom < 0 else "in")]
    else:
        parts = [reframe_chain(clip.get("reframe", "crop"), w, h, tag)]

    grade = clip.get("grade", "none")
    if grade and grade != "none":
        if grade not in GRADES:
            raise ValueError(f"unknown grade: {grade!r}")
        parts.append(GRADES[grade])

    speed = float(clip.get("speed", 1.0))
    if speed != 1.0:
        parts.append(f"setpts=PTS/{speed}")

    parts.append(f"fps={fps}")
    parts.append("setsar=1")
    return ",".join(p for p in parts if p)


def atempo_chain(speed: float) -> str:
    """atempo only accepts 0.5-2.0 per instance, so cascade for extremes."""
    if speed == 1.0:
        return ""
    stages, remaining = [], speed
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={s:.6f}" for s in stages)


def clip_audio_chain(clip: dict, sample_rate: int = 48000) -> str:
    parts = [f"aformat=sample_rates={sample_rate}:channel_layouts=stereo"]
    tempo = atempo_chain(float(clip.get("speed", 1.0)))
    if tempo:
        parts.append(tempo)
    gain = float(clip.get("gain_db", 0) or 0)
    if gain:
        parts.append(f"volume={gain}dB")
    if clip.get("mute"):
        parts.append("volume=0")
    return ",".join(parts)
