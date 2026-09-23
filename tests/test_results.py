import unittest
from unittest.mock import patch
import io
import json

from fastapi import UploadFile
from PIL import Image

from app.pairing import PairingState
from app.results import ResultStore


class ResultStoreTests(unittest.TestCase):
    def test_latest_answer_replaces_previous_and_version_increases(self):
        store = ResultStore()
        self.assertEqual(store.snapshot(), {"version": 0, "answer": None, "updated_at": None})
        store.publish("Erste Antwort")
        first = store.snapshot()
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["answer"], "Erste Antwort")
        self.assertIsNotNone(first["updated_at"])
        store.publish("Zweite Antwort")
        second = store.snapshot()
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["answer"], "Zweite Antwort")

    def test_successful_analysis_is_available_to_watch(self):
        from app import server

        photo = io.BytesIO()
        Image.new("RGB", (4, 4), "white").save(photo, format="JPEG")
        photo.seek(0)
        upload = UploadFile(file=photo, filename="test.jpg")
        with patch.object(server, "RESULTS", ResultStore()), patch.object(
            server, "analyze_image", return_value="Antwort für die Uhr"
        ):
            response = server.analyze(upload)
            result = json.loads(server.watch_result("test-token").body)
        self.assertEqual(response["answer"], "Antwort für die Uhr")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["answer"], "Antwort für die Uhr")

    def test_new_pairing_starts_without_an_old_answer(self):
        from app import server

        store = ResultStore()
        pairing = PairingState()
        with patch.object(server, "RESULTS", store), patch.object(server, "PAIRING", pairing):
            first_code = pairing.generate()
            first_token = json.loads(server.pair(server.PairRequest(code=first_code)).body)["token"]
            store.publish("Alte Antwort")
            self.assertEqual(json.loads(server.watch_result(first_token).body)["answer"], "Alte Antwort")

            server.disconnect(first_token)
            self.assertFalse(pairing.valid(first_token))
            next_code = pairing.generate()
            next_token = json.loads(server.pair(server.PairRequest(code=next_code)).body)["token"]
            self.assertIsNone(json.loads(server.watch_result(next_token).body)["answer"])

            store.publish("Neue Antwort")
            self.assertEqual(json.loads(server.watch_result(next_token).body)["answer"], "Neue Antwort")
