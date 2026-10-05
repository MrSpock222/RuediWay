import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import cv2 as cv
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import server
from app.camera import CameraState
from app.live import LiveView
from app.scanning.engine import ScanEngine
from app.scanning.geometry import corners, project
from app.scanning.quality import assess, quality_map
from app.scanning.routes import scan_router
from app.scanning.service import ScanManager
from scan_fixtures import document, overview, view, jpeg, flight


def eventually(predicate, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Background worker did not reach the expected state")


class AcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = document(decorations=False)
        cls.first, cls.quad = overview(cls.page)

    def initialized(self):
        engine = ScanEngine()
        self.assertTrue(engine.initialize(self.first, self.quad, "manual"))
        return engine

    def test_unknown_boundary_and_blur_cannot_report_coverage(self):
        engine = ScanEngine()
        for data in [b"broken", jpeg(np.full((960, 1280, 3), 255, np.uint8)),
                     jpeg(cv.GaussianBlur(self.first, (61, 61), 20))]:
            engine.feed(data)
        self.assertIsNone(engine.snapshot()["coverage"])
        self.assertEqual(engine.rejected, 3)
        self.assertEqual(engine.state, "seeking_page")

    def test_automatic_boundary_requires_stability_and_is_only_overview(self):
        engine = ScanEngine()
        data = jpeg(self.first)
        engine.feed(data)
        engine.feed(data)
        self.assertIsNone(engine.page_size)
        engine.feed(data)
        self.assertEqual(engine.state, "scanning")
        self.assertEqual(engine.boundary_source, "detected")
        self.assertEqual(engine.coverage, 0)
        self.assertEqual(engine.seen_coverage, 100)

    def test_manual_boundary_is_bound_to_the_frozen_image(self):
        engine = ScanEngine()
        engine.feed(jpeg(self.first))
        engine.freeze()
        points = (self.quad / [1280, 960]).tolist()
        with self.assertRaises(ValueError):
            engine.set_boundary(points, engine.frozen_id + 1)
        with self.assertRaises(ValueError):
            engine.set_boundary([[0, 0]] * 4, engine.frozen_id)
        engine.set_boundary(points, engine.frozen_id)
        self.assertEqual(engine.boundary_source, "manual")

    def test_duplicates_and_unrelated_image_do_not_inflate_coverage(self):
        engine = self.initialized()
        image = jpeg(view(self.page, 0, 0, 800, 600, tilt=5))
        engine.feed(image)
        before = engine.coverage, engine.accepted
        for _ in range(4):
            engine.feed(image)
        self.assertEqual((engine.coverage, engine.accepted), before)
        unrelated = np.random.default_rng(19).integers(0, 255, (960, 1280, 3), np.uint8)
        engine.feed(jpeg(unrelated))
        self.assertEqual((engine.coverage, engine.accepted), before)
        self.assertTrue(engine.snapshot()["tracking_lost"])

    def test_upscaled_overview_and_known_blurred_text_cannot_be_good_blank(self):
        gray = cv.cvtColor(self.first, cv.COLOR_BGR2GRAY)
        scores, _ = quality_map(gray, np.diag([3., 3., 1.]))
        self.assertEqual(float(scores.max()), 0)
        blank = np.full((160, 160), 210, np.uint8)
        known_text = np.ones_like(blank)
        scores, _ = quality_map(blank, np.eye(3), expected_ink=known_text)
        self.assertLess(float(scores.max()), 0.65)

    def test_readable_low_contrast_text_with_white_margins_is_not_rejected(self):
        page = np.full((480, 640), 210, np.uint8)
        for y in (100, 150, 200):
            cv.putText(page, "Readable text 123 ABC", (55, y), cv.FONT_HERSHEY_SIMPLEX, .8, 145, 2, cv.LINE_AA)
        ink = (page < 185).astype(np.uint8)
        readable = cv.GaussianBlur(page, (0, 0), 1.0)
        quality = assess(readable)
        self.assertLess(quality["sharpness"], 18)  # old global gate rejected this
        self.assertTrue(quality["ok"])
        scores, _ = quality_map(readable, np.eye(3), expected_ink=ink)
        self.assertGreater(float((scores[ink > 0] >= .65).mean()), .9)
        blurred = cv.GaussianBlur(page, (0, 0), 4.0)
        bad, _ = quality_map(blurred, np.eye(3), expected_ink=ink)
        self.assertLess(float((bad[ink > 0] >= .65).mean()), .1)

    def test_resolution_diagnostics_explain_unusable_distant_frames(self):
        gray = cv.cvtColor(self.first, cv.COLOR_BGR2GRAY)
        diagnostics = {}
        quality_map(gray, np.diag([3., 3., 1.]), diagnostics=diagnostics)
        self.assertLess(diagnostics["median_density"], .6)
        self.assertEqual(diagnostics["usable_frame_percent"], 0)

    def test_partial_save_preserves_sources_and_labels_missing_detail(self):
        engine = self.initialized()
        engine.feed(jpeg(view(self.page, 0, 0, 800, 600, tilt=5)))
        with tempfile.TemporaryDirectory() as directory:
            folder = engine.finish(directory)
            metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["state"], "partial")
            self.assertLess(metadata["coverage"], 90)
            self.assertIsNone(metadata["ocr_text"])
            self.assertTrue(any(cell["state"] == "weak" for cell in metadata["quality_grid"]))
            self.assertEqual(len(list((folder / "sources").glob("*.jpg"))), engine.accepted)
            with zipfile.ZipFile(folder / "scan.zip") as bundle:
                self.assertIsNone(bundle.testzip())
                self.assertIn("page.png", bundle.namelist())
                self.assertIn("metadata.json", bundle.namelist())
            with self.assertRaises(ValueError):
                engine.finish(directory)

    def test_complete_camera_flight_preserves_geometry_and_finishes_without_ai(self):
        engine = self.initialized()
        page_matrix = engine.keyframes[0].matrix @ cv.getPerspectiveTransform(corners(self.page.shape), self.quad)
        max_error = 0
        with patch.object(server, "analyze_image") as ai:
            for x, y, width, height in flight():
                accepted = engine.accepted
                engine.feed(jpeg(view(self.page, x, y, width, height, tilt=5)))
                if engine.accepted > accepted:
                    matrix = np.array(engine.sources[-1]["metadata"]["homography_to_page"])
                    actual = project(corners((960, 1280)), matrix)
                    expected = project([[x, y], [x+width, y+5], [x+width-5, y+height], [x, y+height-5]], page_matrix)
                    max_error = max(max_error, float(np.linalg.norm(actual - expected, axis=1).max()))
            for brightness in [1, 2, 3]:
                engine.feed(jpeg(cv.convertScaleAbs(view(self.page, 250, 900, 1000, 750, tilt=5), beta=brightness)))
            self.assertEqual(engine.state, "complete", engine.snapshot())
            self.assertGreaterEqual(engine.coverage, 98)
            self.assertLess(max_error, 12, "Unstable extrapolation at frame corners")
            self.assertLessEqual(len(engine.keyframes), 25)
            with tempfile.TemporaryDirectory() as directory:
                folder = engine.finish(directory)
                self.assertEqual(json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["state"], "complete")
            ai.assert_not_called()


class ScanServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.live = LiveView()
        self.manager = ScanManager(self.directory.name, self.live.stop_session, lambda: self.live.control()["session"])

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def start(self):
        return self.manager.start(self.live.start(profile="scan"))

    def test_pause_resume_and_lease_expiry_preserve_scan(self):
        state = self.start()
        engine = self.manager.engine
        self.manager.pause()
        self.assertEqual(self.manager.snapshot()["state"], "paused")
        self.assertIsNone(self.live.control()["session"])
        self.manager.resume(state["id"], self.live.start(profile="scan"))
        self.assertIs(self.manager.engine, engine)
        self.live.stop()
        eventually(lambda: self.manager.snapshot()["state"] == "paused")
        self.assertIs(self.manager.engine, engine)

    def test_bounded_mailbox_cancel_and_old_worker_result(self):
        state = self.start()
        old = self.manager.engine
        with self.manager.condition:
            self.manager.submit(self.manager.session, b"one")
            for _ in range(20):
                self.manager.last_submit = 0
                self.manager.submit(self.manager.session, b"latest")
            self.assertEqual(self.manager.pending[1], b"latest")
            self.assertEqual(self.manager.dropped, 20)
            self.manager.cancel(state["id"])
        new = self.start()
        self.manager._publish(old)
        self.assertEqual(self.manager.snapshot()["id"], new["id"])
        self.manager.submit("expired-session", b"ignored")
        self.assertIsNone(self.manager.pending)

    def test_async_finish_and_download_paths(self):
        state = self.start()
        page = document()
        first, quad = overview(page)
        self.manager.engine.initialize(first, quad, "manual")
        self.manager.command(state["id"], "finish")
        eventually(lambda: self.manager.snapshot().get("saved"), seconds=10)
        self.assertIsNone(self.live.control()["session"])
        self.assertIsNotNone(self.manager.saved_path(state["id"], "page.png"))
        self.assertIsNone(self.manager.saved_path("../other", "page.png"))
        self.assertIsNone(self.manager.saved_path(state["id"], "../../secret"))

    def test_idle_worker_accepts_next_frame_without_fixed_rate_limit(self):
        self.start()
        engine = self.manager.engine
        with patch.object(engine, "feed"), patch.object(self.manager, "_publish"):
            for n in range(5):
                self.manager.submit(self.manager.session, bytes([n]))
                eventually(lambda: self.manager.processed_frames == n + 1)
        metrics = self.manager.snapshot()["pipeline"]
        self.assertEqual(metrics["received"], 5)
        self.assertEqual(metrics["processed"], 5)
        self.assertEqual(metrics["superseded"], 0)
        self.assertGreater(metrics["fps"], 1.8)

    def test_busy_worker_skips_backlog_and_processes_latest_frame(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        received = []
        def feed(raw):
            received.append(raw)
            if raw == b"first":
                entered.set()
                release.wait(5)
        self.start()
        with patch.object(self.manager.engine, "feed", side_effect=feed), patch.object(self.manager, "_publish"):
            self.manager.submit(self.manager.session, b"first")
            self.assertTrue(entered.wait(2))
            try:
                for n in range(40):
                    self.manager.submit(self.manager.session, bytes([n]))
                self.assertEqual(self.manager.pending[1], bytes([39]))
            finally:
                release.set()
            eventually(lambda: self.manager.processed_frames == 2)
        self.assertEqual(received, [b"first", bytes([39])])
        self.assertEqual(self.manager.snapshot()["pipeline"]["superseded"], 39)

    def test_reduced_preview_is_displayed_but_never_enters_scanner(self):
        self.start()
        camera = CameraState()
        camera.register("camera")
        raw = jpeg(np.full((256, 320, 3), 220, np.uint8))
        class Request:
            async def stream(self):
                yield raw
        session = self.live.control()["session"]
        with patch.object(server, "CAMERA", camera), patch.object(server, "LIVE", self.live), \
             patch.object(server, "SCANNER", self.manager), patch.object(server, "analyze_image") as ai:
            asyncio.run(server.live_upload(session, 0, Request(), "camera", "1"))
            self.assertTrue(self.live.status()["receiving"])
            self.assertEqual(self.manager.input_frames, 0)
            self.assertEqual(self.manager.engine.frame_count, 0)
            ai.assert_not_called()

    def test_interrupted_upload_cannot_replace_preview_or_reach_scanner(self):
        from starlette.requests import ClientDisconnect
        self.start()
        camera = CameraState()
        camera.register("camera")
        session = self.live.control()["session"]
        self.live.publish(session, 0, b"previous", "")
        class Request:
            async def stream(self):
                yield b"incomplete"
                raise ClientDisconnect()
        with patch.object(server, "CAMERA", camera), patch.object(server, "LIVE", self.live), \
             patch.object(server, "SCANNER", self.manager):
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(server.live_upload(session, 0, Request(), "camera"))
            self.assertEqual(exc.exception.status_code, 400)
            self.assertEqual(self.live.frame, b"previous")
            self.assertEqual(self.manager.input_frames, 0)

    def test_camera_upload_preserves_native_pixels_and_never_invokes_ai(self):
        self.start()
        camera = CameraState()
        camera.register("camera")
        first, _ = overview(document())
        raw = jpeg(first)
        class Request:
            async def stream(self):
                yield raw[:1000]
                yield raw[1000:]
        session = self.live.control()["session"]
        with patch.object(server, "CAMERA", camera), patch.object(server, "LIVE", self.live), \
             patch.object(server, "SCANNER", self.manager), patch.object(server, "analyze_image") as ai:
            asyncio.run(server.live_upload(session, 0, Request(), "camera"))
            eventually(lambda: self.manager.snapshot().get("received") == 1)
            self.assertEqual(self.manager.engine.latest.shape[:2], (960, 1280))
            preview = cv.imdecode(np.frombuffer(self.live.frame, np.uint8), cv.IMREAD_COLOR)
            self.assertLessEqual(max(preview.shape[:2]), 800)
            ai.assert_not_called()

    def test_host_routes_enforce_access_and_do_not_replace_an_active_lease(self):
        camera = CameraState()
        camera.register("camera")
        camera.heartbeat("camera")
        application = FastAPI()
        application.include_router(scan_router(self.manager, self.live, camera, server.local_only,
                                               lambda: False, Path(__file__).parents[1] / "static"))
        client = TestClient(application, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
        self.assertEqual(client.get("/host/scanner").status_code, 200)
        response = client.post("/host/scan/start")
        self.assertEqual(response.status_code, 200)
        scan_id, session = response.json()["id"], self.live.control()["session"]
        self.assertEqual(client.post("/host/scan/start").status_code, 409)
        self.assertEqual(self.live.control()["session"], session)
        self.assertEqual(client.post(f"/host/scan/{scan_id}/boundary", json={"frame_id":1,"points":[[-1,0]]*4}).status_code, 422)
        self.assertEqual(client.post(f"/host/scan/{scan_id}/pause", headers={"Origin":"http://evil.example"}).status_code, 403)
        remote = TestClient(application, base_url="http://127.0.0.1", client=("192.168.1.50", 50000))
        for route in ["/host/scanner", "/host/scan/status", "/host/scans", f"/host/scans/{scan_id}/metadata.json"]:
            self.assertEqual(remote.get(route).status_code, 403)
        self.assertEqual(client.post(f"/host/scan/{scan_id}/pause").status_code, 200)
        self.assertEqual(client.post(f"/host/scan/{scan_id}/resume").status_code, 200)
        self.assertEqual(self.live.control()["profile"], "scan")


if __name__ == "__main__":
    unittest.main()
