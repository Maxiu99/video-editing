"""Render an edit-decision list into a Facebook Reels-ready MP4.

Three encoding passes:
  1. each clip is trimmed, reframed, graded and normalised to the canvas
  2. the clips are joined (stream copy for cuts, xfade when a transition
     is asked for)
  3. one final pass composites b-roll, brand furniture and captions, mixes
     the music bed under ducked dialogue, and encodes to delivery spec
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import captions as cap_mod
from .ffmpeg_util import ffmpeg, ffmpeg_bin, probe
from .timeline import (GRADES, clip_audio_chain, clip_video_chain,
                       parse_time, reframe_chain)

# Delivery spec. Facebook Reels accepts up to 90s of 9:16 H.264.
CANVAS_W, CANVAS_H = 1080, 1920
FPS = 30
MAX_SECONDS = 90
SAMPLE_RATE = 48000


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------
# EDL loading


def load_edl(path: str) -> tuple[dict, str]:
    with open(path) as fh:
        edl = json.load(fh)
    return edl, os.path.dirname(os.path.abspath(path)) or "."


def resolve(root: str, path: str) -> str:
    full = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.exists(full):
        raise BuildError(f"missing media file: {path}")
    return full


# --------------------------------------------------------------------------
# Pass 1 -- per-clip segments


def render_segments(edl: dict, root: str, build_dir: str,
                    w: int, h: int, fps: int, verbose: bool) -> list[dict]:
    clips = edl.get("clips") or []
    if not clips:
        raise BuildError("the EDL has no clips")

    default_source = edl.get("source")
    segments = []

    for i, clip in enumerate(clips):
        src_rel = clip.get("source", default_source)
        if not src_rel:
            raise BuildError(f"clip {i} has no source and no top-level source")
        src = resolve(root, src_rel)
        info = probe(src)

        start = parse_time(clip.get("in", 0))
        if "out" in clip:
            src_dur = parse_time(clip["out"]) - start
        elif "dur" in clip:
            src_dur = parse_time(clip["dur"])
        else:
            src_dur = max(0.0, info.duration - start)
        if src_dur <= 0:
            raise BuildError(f"clip {i} has non-positive duration")

        speed = float(clip.get("speed", 1.0))
        out_dur = src_dur / speed

        vchain = clip_video_chain(clip, w, h, fps, src_dur, tag=f"c{i}")
        achain = clip_audio_chain(clip, SAMPLE_RATE)
        out_path = os.path.join(build_dir, f"seg_{i:03d}.mp4")

        args = ["-ss", f"{start:.3f}", "-t", f"{src_dur:.3f}", "-i", src]
        if info.has_audio and not clip.get("mute"):
            args += ["-filter_complex",
                     f"[0:v]{vchain}[v];[0:a]{achain}[a]",
                     "-map", "[v]", "-map", "[a]"]
        else:
            # Silent placeholder keeps every segment stream-compatible.
            args += ["-f", "lavfi", "-t", f"{out_dur:.3f}",
                     "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
                     "-filter_complex", f"[0:v]{vchain}[v]",
                     "-map", "[v]", "-map", "1:a"]

        args += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
            "-video_track_timescale", str(fps * 1000),
            out_path,
        ]
        if verbose:
            print(f"  clip {i}: {os.path.basename(src)} "
                  f"{start:.2f}s +{src_dur:.2f}s -> {out_dur:.2f}s "
                  f"({clip.get('reframe', 'crop')}"
                  f"{', x' + str(speed) if speed != 1 else ''})")
        ffmpeg(args)
        segments.append({
            "path": out_path,
            "duration": probe(out_path).duration or out_dur,
            "transition": clip.get("transition", "cut"),
            "transition_dur": float(clip.get("transition_dur", 0.35)),
        })
    return segments


# --------------------------------------------------------------------------
# Pass 2 -- join


def join_segments(segments: list[dict], build_dir: str, fps: int,
                  verbose: bool) -> str:
    out_path = os.path.join(build_dir, "joined.mp4")
    if len(segments) == 1:
        shutil.copy(segments[0]["path"], out_path)
        return out_path

    # A transition on clip i applies to the cut *into* clip i.
    uses_xfade = any(s["transition"] != "cut" for s in segments[1:])
    if not uses_xfade:
        list_path = os.path.join(build_dir, "concat.txt")
        with open(list_path, "w") as fh:
            for seg in segments:
                fh.write(f"file '{os.path.abspath(seg['path'])}'\n")
        ffmpeg(["-f", "concat", "-safe", "0", "-i", list_path,
                "-c", "copy", out_path])
        if verbose:
            print(f"  joined {len(segments)} clips (hard cuts, stream copy)")
        return out_path

    inputs, vfilters, afilters = [], [], []
    for seg in segments:
        inputs += ["-i", seg["path"]]

    v_prev, a_prev = "0:v", "0:a"
    total = segments[0]["duration"]
    for i, seg in enumerate(segments[1:], start=1):
        kind = seg["transition"]
        dur = seg["transition_dur"] if kind != "cut" else 0.0
        dur = min(dur, total - 0.05, seg["duration"] - 0.05)
        dur = max(dur, 0.0)
        v_out, a_out = f"vx{i}", f"ax{i}"
        if dur <= 0:
            vfilters.append(f"[{v_prev}][{i}:v]concat=n=2:v=1:a=0[{v_out}]")
            afilters.append(f"[{a_prev}][{i}:a]concat=n=2:v=0:a=1[{a_out}]")
            total += seg["duration"]
        else:
            offset = total - dur
            xfade = "fade" if kind == "cut" else kind
            vfilters.append(
                f"[{v_prev}][{i}:v]xfade=transition={xfade}:"
                f"duration={dur:.3f}:offset={offset:.3f}[{v_out}]")
            afilters.append(
                f"[{a_prev}][{i}:a]acrossfade=d={dur:.3f}:c1=tri:c2=tri"
                f"[{a_out}]")
            total += seg["duration"] - dur
        v_prev, a_prev = v_out, a_out

    graph = ";".join(vfilters + afilters)
    ffmpeg([*inputs, "-filter_complex", graph,
            "-map", f"[{v_prev}]", "-map", f"[{a_prev}]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
            out_path])
    if verbose:
        print(f"  joined {len(segments)} clips with transitions "
              f"-> {total:.2f}s")
    return out_path


# --------------------------------------------------------------------------
# Pass 3 -- composite, caption, mix, deliver


def broll_branch(idx: int, spec: dict, root: str, w: int, h: int,
                 fps: int) -> tuple[str, str, str]:
    """Return (input path, filter producing [brN], overlay expression)."""
    src = resolve(root, spec["file"])
    at = parse_time(spec.get("at", 0))
    dur = parse_time(spec.get("dur", 2.0))
    src_in = parse_time(spec.get("in", 0))
    mode = spec.get("mode", "full")
    label = f"br{idx}"

    if mode == "full":
        geom = reframe_chain(spec.get("reframe", "crop"), w, h, f"b{idx}")
        overlay = "0:0"
    elif mode == "pip":
        pw = int(spec.get("width", 460))
        ph = int(pw * 16 / 9) if spec.get("vertical") else int(pw * 9 / 16)
        pad = 8
        geom = (f"scale={pw}:{ph}:force_original_aspect_ratio=increase,"
                f"crop={pw}:{ph},"
                f"pad={pw + pad * 2}:{ph + pad * 2}:{pad}:{pad}:white")
        x = spec.get("x", w - pw - 60)
        y = spec.get("y", 300)
        overlay = f"{x}:{y}"
    elif mode == "split":
        geom = (f"scale={w}:{h // 2}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h // 2}")
        overlay = f"0:{h // 2}"
    else:
        raise BuildError(f"unknown b-roll mode: {mode!r}")

    fade = float(spec.get("fade", 0.0))
    fade_chain = ""
    if fade > 0:
        fade_chain = (f",fade=t=in:st=0:d={fade}:alpha=1,"
                      f"fade=t=out:st={max(0.0, dur - fade):.3f}:d={fade}"
                      f":alpha=1")

    chain = (
        f"[{idx}:v]trim=start={src_in:.3f}:duration={dur:.3f},"
        f"{geom},fps={fps},setsar=1,format=yuva420p{fade_chain},"
        f"setpts=PTS-STARTPTS+{at:.3f}/TB[{label}]"
    )
    enable = f"enable='between(t,{at:.3f},{at + dur:.3f})'"
    return src, chain, f"{overlay}:{enable}:eof_action=pass"


def guides_chain(w: int, h: int) -> str:
    """Draw Facebook's UI-occluded regions so framing can be checked."""
    top = int(h * cap_mod.SAFE_TOP)
    bottom = int(h * cap_mod.SAFE_BOTTOM)
    right = int(w * 0.16)
    return (
        f"drawbox=x=0:y=0:w={w}:h={top}:color=red@0.28:t=fill,"
        f"drawbox=x=0:y={h - bottom}:w={w}:h={bottom}:color=red@0.28:t=fill,"
        f"drawbox=x={w - right}:y={top}:w={right}:h={h - top - bottom}"
        f":color=orange@0.22:t=fill"
    )


def build_final(edl: dict, root: str, joined: str, build_dir: str,
                out_path: str, w: int, h: int, fps: int,
                guides: bool, preview: bool, verbose: bool) -> None:
    total = probe(joined).duration

    inputs = ["-i", joined]
    v_filters, a_filters = [], []
    next_idx = 1

    # --- b-roll -----------------------------------------------------------
    v_label = "base"
    v_filters.append(f"[0:v]setsar=1[{v_label}]")
    for spec in edl.get("broll") or []:
        src, chain, overlay = broll_branch(next_idx, spec, root, w, h, fps)
        inputs += ["-i", src]
        v_filters.append(chain)
        nxt = f"ov{next_idx}"
        v_filters.append(f"[{v_label}][br{next_idx}]overlay={overlay}[{nxt}]")
        v_label = nxt
        next_idx += 1

    # --- brand furniture --------------------------------------------------
    brand = edl.get("brand") or {}
    if brand.get("logo"):
        logo = resolve(root, brand["logo"])
        inputs += ["-i", logo]
        lw = int(brand.get("logo_width", 190))
        v_filters.append(f"[{next_idx}:v]scale={lw}:-1[logo]")
        lx = brand.get("logo_x", 60)
        ly = brand.get("logo_y", int(h * cap_mod.SAFE_TOP) + 20)
        nxt = f"ov{next_idx}"
        v_filters.append(f"[{v_label}][logo]overlay={lx}:{ly}[{nxt}]")
        v_label = nxt
        next_idx += 1

    if brand.get("progress_bar"):
        colour = brand.get("progress_color", "yellow")
        v_filters.append(
            f"[{v_label}]drawbox=x=0:y=0:w='iw*t/{total:.3f}':h=10"
            f":color={colour}@0.92:t=fill[prog]")
        v_label = "prog"

    grade = edl.get("grade")
    if grade and grade != "none":
        if grade not in GRADES:
            raise BuildError(f"unknown grade: {grade!r}")
        v_filters.append(f"[{v_label}]{GRADES[grade]}[graded]")
        v_label = "graded"

    # --- captions ---------------------------------------------------------
    caption_list = list(edl.get("captions") or [])
    if brand.get("handle"):
        caption_list.append({
            "t": 0.0, "d": total, "text": brand["handle"], "style": "label",
            "size": 40, "margin_v": int(h * cap_mod.SAFE_BOTTOM) + 30,
            "anim": "none",
        })
    if caption_list:
        for warning in cap_mod.check_safe_zone(caption_list, h):
            print(f"  ! {warning}")
        ass_path = os.path.join(build_dir, "captions.ass")
        with open(ass_path, "w") as fh:
            fh.write(cap_mod.build_ass(caption_list, w, h))
        escaped = ass_path.replace("\\", "/").replace(":", r"\:")
        v_filters.append(f"[{v_label}]subtitles='{escaped}'[capped]")
        v_label = "capped"

    if guides:
        v_filters.append(f"[{v_label}]{guides_chain(w, h)}[guided]")
        v_label = "guided"

    # --- audio ------------------------------------------------------------
    voice_gain = float((edl.get("audio") or {}).get("voice_db", 0))
    a_filters.append(
        f"[0:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
        f"{f',volume={voice_gain}dB' if voice_gain else ''}[voice]")
    mix_labels = ["voice"]

    music = edl.get("music")
    if music and music.get("file"):
        mpath = resolve(root, music["file"])
        inputs += ["-stream_loop", "-1", "-i", mpath]
        gain = float(music.get("gain_db", -19))
        fade_out = float(music.get("fade_out", 1.2))
        chain = (
            f"[{next_idx}:a]aformat=sample_rates={SAMPLE_RATE}:"
            f"channel_layouts=stereo,atrim=0:{total:.3f},"
            f"asetpts=PTS-STARTPTS,volume={gain}dB,"
            f"afade=t=in:st=0:d=0.6,"
            f"afade=t=out:st={max(0.0, total - fade_out):.3f}:d={fade_out}"
        )
        if music.get("duck", True):
            # Split the voice so it can key the compressor as well as be heard.
            a_filters.append("[voice]asplit=2[voice_out][voice_key]")
            mix_labels = ["voice_out"]
            a_filters.append(f"{chain}[musicraw]")
            a_filters.append(
                "[musicraw][voice_key]sidechaincompress="
                "threshold=0.045:ratio=9:attack=12:release=380:makeup=1"
                "[music]")
        else:
            a_filters.append(f"{chain}[music]")
        mix_labels.append("music")
        next_idx += 1

    for k, sfx in enumerate(edl.get("sfx") or []):
        spath = resolve(root, sfx["file"])
        inputs += ["-i", spath]
        at_ms = int(parse_time(sfx.get("at", 0)) * 1000)
        gain = float(sfx.get("gain_db", -6))
        label = f"sfx{k}"
        a_filters.append(
            f"[{next_idx}:a]aformat=sample_rates={SAMPLE_RATE}:"
            f"channel_layouts=stereo,volume={gain}dB,"
            f"adelay={at_ms}:all=1[{label}]")
        mix_labels.append(label)
        next_idx += 1

    if len(mix_labels) > 1:
        joined_labels = "".join(f"[{m}]" for m in mix_labels)
        a_filters.append(
            f"{joined_labels}amix=inputs={len(mix_labels)}:duration=first:"
            f"normalize=0:dropout_transition=0[mixed]")
        a_label = "mixed"
    else:
        a_label = mix_labels[0]

    # Reels are watched at a fixed system volume; normalise to the platform
    # target so the reel is not quieter than everything around it.
    target = float((edl.get("audio") or {}).get("lufs", -14))
    a_filters.append(
        f"[{a_label}]loudnorm=I={target}:TP=-1.5:LRA=11,"
        f"alimiter=limit=0.97[aout]")

    if preview:
        # Must live inside the complex graph -- ffmpeg refuses to attach a
        # simple -vf to a stream that already comes out of one.
        v_filters.append(f"[{v_label}]scale=540:-2[small]")
        v_label = "small"

    graph = ";".join(v_filters + a_filters)
    crf = "26" if preview else str(edl.get("crf", 20))
    preset = "veryfast" if preview else edl.get("preset", "medium")

    ffmpeg([
        *inputs,
        "-filter_complex", graph,
        "-map", f"[{v_label}]", "-map", "[aout]",
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.2",
        "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", str(fps * 2), "-keyint_min", str(fps),
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart",
        "-metadata", f"title={edl.get('title', 'reel')}",
        out_path,
    ], quiet=not verbose)


# --------------------------------------------------------------------------


def report(out_path: str) -> None:
    info = probe(out_path)
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"\n  {out_path}")
    print(f"  {info.width}x{info.height} @ {info.fps:.2f}fps  "
          f"{info.duration:.2f}s  {size_mb:.1f} MB")
    if info.duration > MAX_SECONDS:
        print(f"  ! {info.duration:.1f}s exceeds the {MAX_SECONDS}s "
              "Reels limit -- trim a clip")
    if (info.width, info.height) != (CANVAS_W, CANVAS_H):
        print(f"  ! not 1080x1920 (preview render?)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="build_reel",
        description="Render an EDL into a Facebook Reels-ready MP4.")
    ap.add_argument("edl", help="path to the edit-decision list JSON")
    ap.add_argument("-o", "--out", help="output path (default out/<title>.mp4)")
    ap.add_argument("--preview", action="store_true",
                    help="fast 540p draft render")
    ap.add_argument("--guides", action="store_true",
                    help="overlay the Facebook UI safe zones")
    ap.add_argument("--keep-build", action="store_true",
                    help="keep intermediate segment files")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    verbose = not args.quiet
    try:
        edl, root = load_edl(args.edl)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read EDL: {exc}", file=sys.stderr)
        return 2

    out_cfg = edl.get("output") or {}
    w = int(out_cfg.get("width", CANVAS_W))
    h = int(out_cfg.get("height", CANVAS_H))
    fps = int(out_cfg.get("fps", FPS))

    title = edl.get("title", "reel").replace(" ", "_")
    out_path = args.out or os.path.join(root, "out", f"{title}.mp4")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    build_dir = os.path.join(root, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)

    try:
        if verbose:
            print(f"ffmpeg: {ffmpeg_bin()}")
            print("[1/3] clips")
        segments = render_segments(edl, root, build_dir, w, h, fps, verbose)
        if verbose:
            print("[2/3] joining")
        joined = join_segments(segments, build_dir, fps, verbose)
        if verbose:
            print("[3/3] b-roll, captions, audio, delivery encode")
        build_final(edl, root, joined, build_dir, out_path, w, h, fps,
                    args.guides, args.preview, verbose=False)
    except (BuildError, RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"\nbuild failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.keep_build and os.path.isdir(build_dir):
            shutil.rmtree(build_dir, ignore_errors=True)

    if verbose:
        report(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
