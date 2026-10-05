import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import cv2 as cv
import numpy as np

from app.scanning.compositing import normalize_paper, merge_regions, supported_pixels
from app.scanning.engine import ScanEngine
from app.scanning.rebuild import rebuild
from scan_fixtures import document, overview


class CompositingTests(unittest.TestCase):
    def test_paper_gain_reduces_illumination_steps_without_erasing_text(self):
        paper = np.full((240, 400, 3), 215, np.uint8)
        cv.putText(paper, "Document ABC 123", (20, 125), cv.FONT_HERSHEY_SIMPLEX, .9, (35, 35, 35), 2, cv.LINE_AA)
        dark = np.clip(paper.astype(float) * [.55, .65, .7], 0, 255).astype(np.uint8)
        light = normalize_paper(paper)
        corrected = normalize_paper(dark)
        self.assertLess(abs(float(light[20:50].mean()) - float(corrected[20:50].mean())), 4)
        self.assertLess(float(corrected[95:130].min()), 90)
        self.assertGreater(float(corrected[20:50].mean() - corrected[95:130].min()), 100)

    def test_feather_is_bounded_and_does_not_change_outside_selected_region(self):
        old = np.full((100, 120, 3), 100, np.uint8)
        incoming = np.full_like(old, 200)
        better = np.zeros((100, 120), bool)
        better[20:80, 30:90] = True
        merge_regions(old, incoming, better, np.ones(better.shape, bool))
        self.assertTrue(np.all(old[~better] == 100))
        self.assertEqual(int(old[50, 60, 0]), 200)
        self.assertTrue(100 < int(old[20, 60, 0]) < 150)

    def test_untracked_margin_is_outside_support(self):
        mask = supported_pixels((300, 500), [[180, 90], [320, 90], [320, 210], [180, 210]])
        self.assertEqual(mask[150, 250], 1)
        self.assertEqual(mask[150, 20], 0)
        self.assertEqual(mask[15, 250], 0)
        self.assertEqual(int(supported_pixels((100, 100), []).sum()), 0)

    def test_repair_keeps_original_package_and_writes_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = ScanEngine(long_side=1000)
            first, quad = overview(document())
            engine.initialize(first, quad, "manual")
            source = engine.finish(directory)
            before = {p.relative_to(source): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob("*") if p.is_file()}
            with contextlib.redirect_stdout(io.StringIO()):
                repaired = rebuild(source, directory)
            self.assertNotEqual(source, repaired)
            after = {p.relative_to(source): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            metadata = json.loads((repaired / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["reconstruction"]["original_scan"], source.name)
            self.assertEqual(metadata["state"], "partial")
            self.assertEqual((source / "sources/0000.jpg").read_bytes(), (repaired / "sources/0000.jpg").read_bytes())


if __name__ == "__main__":
    unittest.main()
