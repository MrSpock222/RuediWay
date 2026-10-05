"""Bounded live preview: one frame, host lease, no recording or AI calls."""
import secrets
import threading
import time

from fastapi import HTTPException


class LiveView:
    def __init__(self):
        self.lock = threading.RLock()
        self.session = None
        self.deadline = 0
        self.revision = 0
        self.frame = None
        self.sequence = 0
        self.seen = None
        self.error = ""
        self.warning = ""
        self.settings = {"mode": "text", "ev": -1.0, "shutter": 0}
        self.profile = "preview"

    def stop(self):
        with self.lock:
            self.session = None
            self.frame = None
            self.seen = None
            self.error = self.warning = ""

    def active(self):
        if self.session and time.monotonic() >= self.deadline:
            self.stop()
        return self.session is not None

    def start(self, profile="preview"):
        with self.lock:
            self.stop()
            self.session = secrets.token_urlsafe(16)
            self.profile = profile
            self.deadline = time.monotonic() + 20
            return self.session

    def require(self, session):
        if not self.active() or session != self.session:
            raise HTTPException(409, "Live-Vorschau beendet. Erneut starten.")

    def keepalive(self, session):
        with self.lock:
            self.require(session)
            self.deadline = time.monotonic() + 20

    def stop_session(self, session):
        with self.lock:
            if session == self.session:
                self.stop()

    def configure(self, settings):
        with self.lock:
            self.settings = dict(settings)
            self.revision += 1
            self.frame = None
            self.seen = None
            self.error = self.warning = ""

    def options(self):
        with self.lock:
            return dict(self.settings)

    def control(self):
        with self.lock:
            return {"session": self.session if self.active() else None,
                    "revision": self.revision, "settings": dict(self.settings), "profile": self.profile}

    def publish(self, session, revision, frame, warning):
        with self.lock:
            self.require(session)
            if revision != self.revision:
                raise HTTPException(409, "Kameraeinstellungen wurden geändert.")
            self.frame = frame
            self.sequence += 1
            self.seen = time.monotonic()
            self.warning = warning
            self.error = ""

    def failure(self, session, message):
        with self.lock:
            self.require(session)
            self.error = message[:300]

    def read(self, session):
        with self.lock:
            self.require(session)
            fresh = self.seen is not None and time.monotonic() - self.seen < 5
            return self.sequence, self.frame if fresh else None

    def status(self):
        with self.lock:
            active = self.active()
            return {"active": active, "receiving": active and self.seen is not None and time.monotonic() - self.seen < 5,
                    "error": self.error, "warning": self.warning}
