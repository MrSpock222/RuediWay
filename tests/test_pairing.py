import unittest
from unittest.mock import patch

from app.pairing import CODE_LIFETIME, MAX_ATTEMPTS, PRESENCE_TIMEOUT, SESSION_LIFETIME, PairingState


class PairingTests(unittest.TestCase):
    def test_code_is_one_use_and_session_expires(self):
        state = PairingState()
        with patch("app.pairing.time.monotonic", return_value=100), patch("app.pairing.secrets.randbelow", return_value=42):
            self.assertEqual(state.generate(), "000042")
            token = state.pair("000042")
            self.assertTrue(state.valid(token))
            with self.assertRaises(ValueError):
                state.pair("000042")
        with patch("app.pairing.time.monotonic", return_value=100 + SESSION_LIFETIME):
            self.assertFalse(state.valid(token))

    def test_code_expires_and_wrong_guesses_invalidate_it(self):
        state = PairingState()
        with patch("app.pairing.time.monotonic", return_value=100), patch("app.pairing.secrets.randbelow", return_value=123456):
            state.generate()
        with patch("app.pairing.time.monotonic", return_value=100 + CODE_LIFETIME):
            with self.assertRaisesRegex(ValueError, "abgelaufen"):
                state.pair("123456")
        with patch("app.pairing.time.monotonic", return_value=200), patch("app.pairing.secrets.randbelow", return_value=123456):
            state.generate()
            for _ in range(MAX_ATTEMPTS - 1):
                with self.assertRaisesRegex(ValueError, "stimmt nicht"):
                    state.pair("000000")
            with self.assertRaisesRegex(ValueError, "Zu viele"):
                state.pair("000000")
            with self.assertRaises(ValueError):
                state.pair("123456")

    def test_new_code_replaces_old_and_disconnect_revokes_session(self):
        state = PairingState()
        with patch("app.pairing.time.monotonic", return_value=100), patch("app.pairing.secrets.randbelow", side_effect=[111111, 222222]):
            state.generate()
            self.assertEqual(state.generate(), "222222")
            with self.assertRaises(ValueError):
                state.pair("111111")
            token = state.pair("222222", result_version=7)
            self.assertEqual(state.result_baseline(token), 7)
            state.revoke(token)
            self.assertFalse(state.valid(token))
            self.assertEqual(state.result_baseline(token), 0)

    def test_presence_tracks_heartbeat_away_and_timeout(self):
        state = PairingState()
        with patch("app.pairing.time.monotonic", return_value=100), patch("app.pairing.secrets.randbelow", return_value=123456):
            state.generate()
            token = state.pair("123456")
            self.assertEqual(state.status(), {"connected": True, "devices": 1, "code_active": False})
            state.mark_away(token)
            self.assertFalse(state.status()["connected"])
        with patch("app.pairing.time.monotonic", return_value=110):
            self.assertTrue(state.touch(token))
            self.assertTrue(state.status()["connected"])
        with patch("app.pairing.time.monotonic", return_value=110 + PRESENCE_TIMEOUT):
            self.assertFalse(state.status()["connected"])
            self.assertTrue(state.valid(token))

    def test_live_connection_disconnects_immediately(self):
        state = PairingState()
        with patch("app.pairing.time.monotonic", return_value=100), patch("app.pairing.secrets.randbelow", return_value=123456):
            state.generate()
            token = state.pair("123456")
            self.assertTrue(state.attach(token, "tab-1"))
            state.mark_away(token)
            self.assertTrue(state.status()["connected"])
            state.detach(token, "tab-1")
            self.assertFalse(state.status()["connected"])
