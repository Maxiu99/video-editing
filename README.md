# Reels cutter

An ffmpeg pipeline that turns raw footage into a Facebook Reels–ready MP4:
vertical reframing, cuts and transitions, b-roll inserts, animated burn-in
captions, a ducked music bed, and a delivery encode at platform spec.

The edit lives in a JSON edit-decision list, so a change to a cut point or a
caption is a one-line diff and a re-render — not a re-do.

```bash
pip install imageio-ffmpeg          # or have ffmpeg on PATH
python3 -m reels.build my.edl.json --preview   # fast 540p draft
python3 -m reels.build my.edl.json             # 1080x1920 delivery
python3 -m reels.build my.edl.json --guides    # draw the FB UI safe zones
python3 -m tests.test_reels                    # unit tests
```

`demo.edl.json` is a working example. `python3 tools/make_demo_media.py`
synthesises stand-in footage for it, so the whole pipeline can be exercised
before real rushes exist.

## Output spec

1080×1920, H.264 high@4.2, yuv420p, 30fps, 2s keyframe interval, AAC 128k
48kHz stereo, `+faststart`, loudness normalised to −14 LUFS / −1.5 dBTP.
The build warns if the result runs past the 90s Reels limit.

## The EDL

```jsonc
{
  "title": "my_reel",
  "source": "raw/interview.mp4",   // default source for every clip
  "grade": "punch",                // whole-reel look
  "preset": "medium",              // x264 preset for the delivery pass
  "crf": 20,

  "clips": [
    { "in": "0:02.5", "out": "0:07.0",
      "reframe": "crop",           // crop | blur | fit
      "zoom": 0.10,                // + pushes in, - pulls out
      "speed": 1.0,
      "grade": "cool",             // per-clip, overrides nothing global
      "gain_db": 0, "mute": false,
      "transition": "fade",        // applies to the cut *into* this clip
      "transition_dur": 0.3,
      "source": "raw/other.mp4"    // optional per-clip override
    }
  ],

  "broll": [
    { "file": "broll/city.mp4", "at": "0:04.3", "dur": 1.8, "in": "0:01.0",
      "mode": "full",              // full | pip | split
      "reframe": "crop", "fade": 0.25 },
    { "file": "broll/chart.mp4", "at": "0:07.5", "dur": 2.2,
      "mode": "pip", "width": 470, "x": 550, "y": 340 }
  ],

  "captions": [
    { "t": 0.15, "d": 2.4, "text": "Stop scrolling", "style": "hook" },
    { "t": 2.7,  "d": 2.6, "text": "line one\\Nline two", "style": "body" },
    { "t": 5.6,  "d": 3.0, "text": "word by word", "style": "karaoke" }
  ],

  "music": { "file": "music/bed.mp3", "gain_db": -20, "duck": true,
             "fade_out": 1.5 },
  "sfx":   [ { "file": "assets/whoosh.wav", "at": "0:04.3", "gain_db": -6 } ],
  "audio": { "lufs": -14, "voice_db": 0 },

  "brand": { "handle": "@yourhandle", "logo": "assets/logo.png",
             "logo_width": 190, "progress_bar": true,
             "progress_color": "#FF2D55" }
}
```

Times accept `7`, `7.5`, `"0:07.5"` or `"1:02.25"`.

### Reframe modes

- `crop` — fill the frame, cut the sides. The default; right for a talking head.
- `blur` — keep the whole frame, sit it on a blurred zoomed copy of itself.
- `fit` — letterbox onto black.

### Caption styles

`hook`, `body`, `karaoke` (per-word highlight), `label`, `statement`. Any
preset field — `size`, `primary`, `outline`, `align`, `margin_v`, `anim`,
`upper`, `font` — can be overridden inline on a single caption.

Captions are rendered through libass, so Chinese works out of the box
(WenQuanYi Zen Hei is picked automatically for CJK text). `\\N` is a line
break.

### Safe zones

Facebook draws its own UI over the reel: roughly the top 11%, the bottom 20%
and a right-hand button rail. The build warns when a caption strays under
that chrome, and `--guides` renders the zones as coloured overlays so framing
can be checked before delivery.

### Transitions

`cut` (default, stream-copied — fast), plus any xfade name: `fade`,
`slideleft`, `slideright`, `slideup`, `wipeleft`, `circleopen`, `circleclose`,
`dissolve`, `pixelize`, `radial`, `smoothleft`…

### Grades

`none`, `punch`, `cool`, `warm`, `bw`.

## How it renders

1. Each clip is trimmed, reframed, zoomed, graded, speed-changed and
   normalised to the canvas as its own segment.
2. Segments are joined — stream copy when every cut is hard, an xfade chain
   when a transition is asked for.
3. One final pass composites b-roll and brand furniture, burns the captions,
   mixes the music under sidechain-ducked dialogue, normalises loudness and
   encodes to delivery spec.

## Notes

- Ken Burns moves are an animated centre crop, not `zoompan`: at `d=1`
  zoompan fails to propagate EOF when an audio stream shares the filter
  graph, and ffmpeg spins generating frames forever.
- The clip is reframed onto an oversampled canvas first, so the tight end of
  a push lands pixel-for-pixel sharp rather than upscaled.
