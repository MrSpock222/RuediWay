import io
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.requests import Request

from app.camera import CameraState
from app.pairing import PairingState
from app.results import ResultStore
from app import server


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.camera = CameraState()
        self.camera.register("camera")

    def ready(self):
        self.camera.heartbeat("camera")
        return self.camera.request("watch")["id"]

    def test_offline_camera_and_client_impersonation_rejected(self):
        with self.assertRaises(HTTPException) as error:
            self.camera.request("watch")
        self.assertEqual(error.exception.status_code, 503)
        with self.assertRaises(HTTPException):
            self.camera.claim("watch")

    def test_host_trigger_rejects_foreign_websites_and_remote_clients(self):
        def request(host="127.0.0.1:8000", origin="http://127.0.0.1:8000", client="127.0.0.1"):
            return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/host/capture",
                            "root_path": "", "query_string": b"", "server": ("127.0.0.1", 8000),
                            "client": (client, 1234), "headers": [(b"host", host.encode()), (b"origin", origin.encode())]})
        server.local_only(request())
        for value in (request(origin="https://unrelated.example"), request(host="unrelated.example"), request(client="192.168.1.2")):
            with self.assertRaises(HTTPException) as error:
                server.local_only(value)
            self.assertEqual(error.exception.status_code, 403)

    def test_only_one_capture_and_one_delivery(self):
        job = self.ready()
        with self.assertRaises(HTTPException):
            self.camera.request("host")
        self.assertEqual(self.camera.claim("camera"), {"id": job})
        self.assertIsNone(self.camera.claim("camera"))
        self.camera.begin("camera", job)
        with self.assertRaises(HTTPException):
            self.camera.begin("camera", job)
        published = []
        self.camera.complete("camera", job, lambda: published.append(True))
        self.assertEqual(published, [True])
        self.assertFalse(self.camera.snapshot()["busy"])

    def test_timeout_does_not_publish_late_result(self):
        job = self.ready()
        self.camera.claim("camera")
        self.camera.begin("camera", job)
        self.camera.deadline = 0
        published = []
        with self.assertRaises(HTTPException):
            self.camera.complete("camera", job, lambda: published.append(True))
        self.assertEqual(published, [])
        self.assertEqual(self.camera.snapshot()["state"], "error")

    def test_disconnect_and_repair_cancel_old_job(self):
        job = self.ready()
        self.camera.claim("camera")
        self.camera.disconnect("watch")
        with self.assertRaises(HTTPException):
            self.camera.begin("camera", job)
        self.camera.register("replacement")
        with self.assertRaises(HTTPException):
            self.camera.claim("camera")
        self.assertFalse(self.camera.snapshot()["online"])

    def test_presence_expires_and_shutdown_is_immediate(self):
        with patch("app.camera.time.monotonic", return_value=100):
            self.camera.heartbeat("camera")
        with patch("app.camera.time.monotonic", return_value=116):
            self.assertFalse(self.camera.snapshot()["online"])
        self.camera.heartbeat("camera")
        self.camera.away("camera")
        self.assertFalse(self.camera.snapshot()["online"])

    def test_pair_capture_upload_watch_flow_and_bad_image_recovery(self):
        with patch.object(server, "CAMERA", self.camera), patch.object(server, "PAIRING", PairingState()), patch.object(server, "RESULTS", ResultStore()), patch.object(server, "analyze_image", return_value="42"):
            code = server.PAIRING.generate()
            token = json.loads(server.pair(server.PairRequest(code=code, device="camera")).body)["token"]
            server.camera_heartbeat(token)
            job = server.host_capture()["id"]
            self.assertEqual(json.loads(server.camera_next(token).body)["job"]["id"], job)
            photo = io.BytesIO()
            Image.new("RGB", (4, 4)).save(photo, "JPEG")
            photo.seek(0)
            server.camera_image(job, UploadFile(file=photo), token)
            self.assertTrue(photo.closed)
            self.assertEqual(json.loads(server.watch_result("watch").body)["answer"], "42")
            job = server.capture("watch")["id"]
            server.camera_next(token)
            with self.assertRaises(HTTPException):
                server.camera_image(job, UploadFile(file=io.BytesIO(b"invalid")), token)
            self.assertEqual(self.camera.snapshot()["state"], "error")
            self.assertEqual(server.RESULTS.snapshot()["version"], 1)
            self.assertIn("id", server.host_capture())

    def test_preview_skips_ai_and_preserves_answer_and_replaces_image(self):
        with patch.object(server, "CAMERA", self.camera), patch.object(server, "RESULTS", ResultStore()), patch.object(server, "analyze_image") as ai:
            server.RESULTS.publish("Vorhandene Antwort")
            before = server.RESULTS.snapshot()
            self.camera.heartbeat("camera")
            previous_id = None
            for color in ("red", "blue"):
                job = server.host_preview_capture()["id"]
                server.camera_next("camera")
                photo = io.BytesIO()
                Image.new("RGB", (4, 4), color).save(photo, "PNG")
                photo.seek(0)
                self.assertEqual(server.camera_image(job, UploadFile(file=photo), "camera"), {"preview": True})
                self.assertTrue(photo.closed)
                response = server.host_preview_image(job)
                self.assertEqual(response.media_type, "image/jpeg")
                self.assertEqual(response.headers["cache-control"], "no-store")
                with Image.open(io.BytesIO(response.body)) as image:
                    self.assertEqual(image.size, (4, 4))
                self.assertEqual(json.loads(server.host_status().body)["preview_id"], job)
                if previous_id:
                    with self.assertRaises(HTTPException):
                        server.host_preview_image(previous_id)
                previous_id = job
            ai.assert_not_called()
            self.assertEqual(server.RESULTS.snapshot(), before)
            self.camera.disconnect("camera")
            with self.assertRaises(HTTPException):
                server.host_preview_image(previous_id)

    def test_invalid_or_expired_preview_never_publishes(self):
        with patch.object(server, "CAMERA", self.camera), patch.object(server, "analyze_image") as ai:
            self.camera.heartbeat("camera")
            job = server.host_preview_capture()["id"]
            server.camera_next("camera")
            upload = UploadFile(file=io.BytesIO(b"invalid"))
            with self.assertRaises(HTTPException):
                server.camera_image(job, upload, "camera")
            self.assertTrue(upload.file.closed)
            self.assertIsNone(self.camera.preview())
            self.assertFalse(self.camera.snapshot()["busy"])
            job = server.host_preview_capture()["id"]
            server.camera_next("camera")
            self.camera.begin("camera", job)
            self.camera.deadline = 0
            with self.assertRaises(HTTPException):
                self.camera.complete_preview("camera", job, b"late image")
            self.assertIsNone(self.camera.preview())
            ai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
