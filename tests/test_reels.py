"""Unit tests for the pieces that do not need ffmpeg to run."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reels import captions as cap  # noqa: E402
from reels import timeline as tl  # noqa: E402


class TimeParsing(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(tl.parse_time(7), 7.0)
        self.assertEqual(tl.parse_time("7.5"), 7.5)
        self.assertEqual(tl.parse_time("0:07.5"), 7.5)
        self.assertEqual(tl.parse_time("1:02.25"), 62.25)
        self.assertEqual(tl.parse_time("1:00:00"), 3600.0)

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            tl.parse_time("later")


class AudioSpeed(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(tl.atempo_chain(1.0), "")

    def test_cascades_beyond_filter_limits(self):
        chain = tl.atempo_chain(4.0)
        self.assertEqual(chain.count("atempo"), 2)
        product = 1.0
        for stage in chain.split(","):
            product *= float(stage.split("=")[1])
        self.assertAlmostEqual(product, 4.0, places=4)

    def test_cascades_for_slow_motion(self):
        chain = tl.atempo_chain(0.25)
        product = 1.0
        for stage in chain.split(","):
            product *= float(stage.split("=")[1])
        self.assertAlmostEqual(product, 0.25, places=4)


class ZoomChain(unittest.TestCase):
    def test_oversamples_so_the_tight_end_is_native(self):
        sw, sh = tl.zoom_frame_size(0.10, 1080, 1920)
        self.assertEqual((sw, sh), (1188, 2112))
        self.assertEqual(sw % 2, 0)
        self.assertEqual(sh % 2, 0)

    def test_no_zoompan(self):
        # zoompan deadlocks at d=1 when audio shares the graph.
        chain = tl.clip_video_chain({"zoom": 0.1}, 1080, 1920, 30, 3.0)
        self.assertNotIn("zoompan", chain)
        self.assertIn("crop=w=", chain)

    def test_commas_inside_expressions_are_escaped(self):
        chain = tl.zoom_chain(0.1, 3.0, 1080, 1920)
        # An unescaped comma inside min() would split the filter chain.
        self.assertIn(r"min(t/3.0000\,1)", chain)


class BlurReframe(unittest.TestCase):
    def test_labels_are_namespaced(self):
        a = tl.reframe_chain("blur", 1080, 1920, "c0")
        b = tl.reframe_chain("blur", 1080, 1920, "c1")
        self.assertIn("[c0bg]", a)
        self.assertIn("[c1bg]", b)
        self.assertNotIn("[c0bg]", b)


class AssOutput(unittest.TestCase):
    def setUp(self):
        self.doc = cap.build_ass([
            {"t": 0.2, "d": 1.5, "text": "Stop scrolling", "style": "hook"},
            {"t": 2.0, "d": 2.0, "text": "three things", "style": "karaoke"},
            {"t": 4.0, "d": 1.0, "text": "两行\\N中文字幕"},
        ])

    def test_dialogue_field_count_matches_format(self):
        fmt = next(l for l in self.doc.splitlines()
                   if l.startswith("Format:") and "Text" in l)
        expected = len(fmt.split(":", 1)[1].split(","))
        for line in self.doc.splitlines():
            if line.startswith("Dialogue:"):
                body = line.split(":", 1)[1]
                # Text is the last field and may itself contain commas.
                self.assertGreaterEqual(len(body.split(",")), expected)
                head = body.split(",")[:expected - 1]
                self.assertEqual(len(head), expected - 1)
                # A stray field here shifts the caption text and prints a
                # leading comma on screen.
                self.assertEqual(head[0].strip(), "0")

    def test_no_leading_comma_in_text(self):
        for line in self.doc.splitlines():
            if line.startswith("Dialogue:"):
                text = line.split(",", 9)[9]
                self.assertFalse(text.startswith(","), line)

    def test_karaoke_timings_sum_to_the_line_duration(self):
        body = cap._karaoke_body("one two three", 2.0)
        total = sum(int(p.split("}")[0]) for p in body.split(r"{\k")[1:])
        self.assertEqual(total, 200)

    def test_cjk_uses_a_font_with_coverage(self):
        self.assertEqual(cap.pick_font("中文"), cap.CJK_FONT)
        self.assertEqual(cap.pick_font("latin"), cap.LATIN_FONT)

    def test_cjk_karaoke_splits_per_character(self):
        self.assertEqual(cap._split_words("三件事"), ["三", "件", "事"])

    def test_playres_matches_the_canvas(self):
        self.assertIn("PlayResX: 1080", self.doc)
        self.assertIn("PlayResY: 1920", self.doc)


class SafeZone(unittest.TestCase):
    def test_flags_caption_under_the_cta_strip(self):
        warnings = cap.check_safe_zone(
            [{"text": "too low", "style": "body", "margin_v": 40}], 1920)
        self.assertEqual(len(warnings), 1)

    def test_default_presets_are_clear(self):
        captions = [{"text": t, "style": s} for t, s in
                    [("a", "hook"), ("b", "body"), ("c", "label")]]
        self.assertEqual(cap.check_safe_zone(captions, 1920), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
