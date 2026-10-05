import asyncio
import io
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageStat
from pydantic import ValidationError

from app import server
from app.camera import CameraState
from app.imaging import prepare_image
from app.live import LiveView
from app.results import ResultStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raspberrypi"))
from video import JpegFrames, exposure_options


def picture(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


class ImagingTests(unittest.TestCase):
    def test_text_filter_does_not_amplify_nearly_blank_sensor_noise(self):
        image = Image.effect_noise((200, 100), 3)
        data, warning = prepare_image(picture(image), "text")
        output = Image.open(io.BytesIO(data))
        self.assertTrue(warning)
        self.assertLess(ImageStat.Stat(output).stddev[0], ImageStat.Stat(image).stddev[0] * 1.2)

    def test_text_mode_increases_faint_letter_contrast(self):
        image = Image.new("RGB", (300, 100), (210, 210, 210))
        draw = ImageDraw.Draw(image)
        draw.text((20, 20), "Black text 123 + 456", fill=(160, 160, 160), font_size=24)
        result, _ = prepare_image(picture(image), "text")
        enhanced = Image.open(io.BytesIO(result))
        self.assertGreater(ImageStat.Stat(enhanced).stddev[0], ImageStat.Stat(image).stddev[0] * 2)

    def test_blank_and_invalid_images_report_limits(self):
        for color, word in (("white", "Weiß"), ("black", "Schwarz")):
            _, warning = prepare_image(picture(Image.new("RGB", (10, 10), color)), "text")
            self.assertIn(word, warning)
        with self.assertRaises(HTTPException):
            prepare_image(b"not a picture")

    def test_black_white_mode_preserves_dark_strokes(self):
        image = Image.new("RGB", (200, 100), "white")
        ImageDraw.Draw(image).rectangle((50, 20, 53, 80), fill=(70, 70, 70))
        data, _ = prepare_image(picture(image), "bw")
        output = Image.open(io.BytesIO(data))
        self.assertLess(output.getpixel((51, 50)), 20)
        self.assertGreater(output.getpixel((100, 50)), 235)


class LiveTests(unittest.TestCase):
    def test_lease_expires_and_discards_frame(self):
        live = LiveView()
        with patch("app.live.time.monotonic", return_value=100):
            session = live.start()
            live.publish(session, 0, b"frame", "")
        with patch("app.live.time.monotonic", return_value=121):
            self.assertFalse(live.status()["active"])
            self.assertIsNone(live.frame)
            with self.assertRaises(HTTPException):
                live.publish(session, 0, b"late", "")

    def test_stale_frames_and_old_sessions_cannot_replace_current(self):
        live = LiveView()
        old = live.start()
        current = live.start()
        live.stop_session(old)
        self.assertTrue(live.status()["active"])
        with self.assertRaises(HTTPException):
            live.publish(old, 0, b"old", "")
        live.configure({"mode": "text", "ev": -2, "shutter": 1000})
        with self.assertRaises(HTTPException):
            live.publish(current, 0, b"old settings", "")
        live.publish(current, 1, b"correct", "warning")
        self.assertEqual(live.read(current)[1], b"correct")
        live.stop_session(current)
        self.assertIsNone(live.frame)

    def test_job_keeps_selected_settings(self):
        camera = CameraState()
        camera.register("camera")
        camera.heartbeat("camera")
        settings = {"mode": "text", "ev": -2, "shutter": 1000}
        job = camera.request("host", settings=settings)
        settings["ev"] = 2
        camera.claim("camera")
        self.assertEqual(camera.job_options("camera", job["id"])["ev"], -2)

    def test_stream_upload_never_calls_ai_and_checks_camera_role(self):
        class Request:
            async def stream(self):
                yield picture(Image.new("RGB", (20, 20), "white"))
        live, camera = LiveView(), CameraState()
        camera.register("camera")
        session = live.start()
        results = ResultStore()
        with patch.object(server, "CAMERA", camera), patch.object(server, "LIVE", live), patch.object(server, "RESULTS", results), patch.object(server, "analyze_image") as ai:
            with self.assertRaises(HTTPException):
                asyncio.run(server.live_upload(session, 0, Request(), "watch"))
            asyncio.run(server.live_upload(session, 0, Request(), "camera"))
            self.assertTrue(live.status()["receiving"])
            self.assertIn("Weiß", live.status()["warning"])
            self.assertEqual(results.snapshot()["version"], 0)
            ai.assert_not_called()

    def test_settings_validation_and_shutter_options(self):
        for change in ({"ev": float("nan")}, {"ev": 10}, {"shutter": 123}, {"mode": "other"}):
            with self.assertRaises(ValidationError):
                server.CameraSettings(**change)
        args = exposure_options({"ev": -2, "shutter": 1000})
        self.assertIn("--shutter", args)
        self.assertEqual(args[-2:], ["--gain", "1"])


class MjpegTests(unittest.TestCase):
    def test_chunk_boundaries_and_multiple_frames(self):
        parser = JpegFrames()
        self.assertEqual(parser.feed(b"noise\xff"), [])
        self.assertEqual(parser.feed(b"\xd8one\xff"), [])
        self.assertEqual(parser.feed(b"\xd9\xff\xd8two\xff\xd9"), [b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"])

    def test_invalid_stream_cannot_grow_without_limit(self):
        with self.assertRaises(RuntimeError):
            JpegFrames().feed(b"\xff\xd8" + b"x" * (2 * 1024 * 1024))


if __name__ == "__main__":
    unittest.main()
