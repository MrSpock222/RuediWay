import json
from pathlib import Path
import tempfile
import unittest

import cv2 as cv
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.scanning.engine import ScanEngine
from app.scanning.routes import scan_router
from app.scanning.text_regions import detect_text, project_text


class TextCoverageTests(unittest.TestCase):
    def engine(self):
        engine = ScanEngine()
        engine.page_size = (600, 800)
        engine.scores = np.zeros((800, 600), np.float32)
        engine.seen = np.ones((800, 600), bool)
        engine.text_target = np.zeros((800, 600), bool)
        return engine

    def test_blank_margins_do_not_need_detail_but_unknown_text_cannot_complete(self):
        engine = self.engine()
        engine.update_grid()
        self.assertEqual(engine.coverage, 0)
        self.assertFalse(engine.text_regions_good())
        engine.text_target[100:130, 100:450] = True
        engine.scores[engine.text_target] = .9
        engine.update_grid()
        self.assertEqual(engine.coverage, 100)
        self.assertTrue(engine.text_regions_good())
        self.assertLess(engine.quality_pixel_coverage, 5)
        self.assertTrue(any(c['state'] == 'ignored' for c in engine.grid))

    def test_small_missing_text_cannot_hide_in_overall_percentage(self):
        engine = self.engine()
        engine.text_target[100:300, 100:450] = True
        engine.scores[engine.text_target] = 1
        engine.text_target[600:610, 100:160] = True
        engine.update_grid()
        self.assertGreater(engine.coverage, 98)
        self.assertFalse(engine.text_regions_good())

    def test_detection_finds_text_but_ignores_blank_paper_and_long_rules(self):
        page = np.full((800, 600), 240, np.uint8)
        cv.rectangle(page, (10, 10), (590, 790), 20, 2)
        cv.line(page, (40, 700), (560, 700), 20, 2)
        self.assertFalse(detect_text(page).any())
        cv.putText(page, 'Sample text 123', (80, 250), cv.FONT_HERSHEY_SIMPLEX, 1, 35, 2)
        detected = detect_text(page)
        self.assertGreater(int(detected[210:260, 70:370].sum()), 3000)
        self.assertFalse(detected[300:].any())

    def test_new_text_expands_target_without_forgetting_existing_text(self):
        engine = self.engine()
        engine.text_target[50:80, 50:400] = True
        engine.scores[engine.text_target] = 1
        engine.update_grid()
        engine.full_confirmations = 2
        original = engine.text_target.copy()
        closeup = np.full((800, 600), 240, np.uint8)
        cv.putText(closeup, 'Another line 123', (70, 600), cv.FONT_HERSHEY_SIMPLEX, 1, 30, 2)
        engine.add_text(closeup, np.eye(3), np.ones_like(closeup))
        engine.update_grid()
        self.assertTrue(engine.text_target[original].all())
        self.assertLess(engine.coverage, 100)
        self.assertEqual(engine.full_confirmations, 0)
        target = engine.text_target.copy()
        engine.add_text(np.full_like(closeup, 240), np.eye(3), np.ones_like(closeup))
        self.assertTrue(np.array_equal(engine.text_target, target))

    def test_unsupported_text_does_not_become_a_scan_target(self):
        gray = np.full((300, 600), 240, np.uint8)
        cv.putText(gray, 'Outside page', (50, 150), cv.FONT_HERSHEY_SIMPLEX, 1, 25, 2)
        support = np.zeros_like(gray)
        support[200:] = 1
        self.assertFalse(project_text(gray, np.eye(3), (600, 300), support).any())

    def test_partial_save_keeps_corrected_mosaic_and_text_metadata(self):
        engine = self.engine()
        engine.text_target[100:130, 100:450] = True
        engine.mosaic = np.full((800, 600, 3), 238, np.uint8)
        engine.mosaic[110:120, 100:450] = 42
        engine.update_grid()
        with tempfile.TemporaryDirectory() as directory:
            saved = engine.finish(directory)
            self.assertTrue(np.array_equal(cv.imread(str(saved / 'page.png')), engine.mosaic))
            metadata = json.loads((saved / 'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['state'], 'partial')
            self.assertEqual(metadata['coverage_basis'], 'text')
            self.assertEqual(len(metadata['text_regions']), 1)
            self.assertIn('illumination', metadata['compositing'])

    def test_saved_history_distinguishes_old_page_from_new_text_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            for i, basis in enumerate([None, 'text']):
                folder = Path(directory) / (str(i) * 32)
                folder.mkdir()
                metadata = dict(id=folder.name, created='2026-09-26', state='partial', coverage=50)
                if basis:
                    metadata['coverage_basis'] = basis
                (folder / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
            class Manager:
                root = Path(directory)
            app = FastAPI()
            app.include_router(scan_router(Manager(), None, None, lambda: None, None, None))
            items = TestClient(app).get('/host/scans').json()
            self.assertEqual({item['coverage_basis'] for item in items}, {'page', 'text'})
