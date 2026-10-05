"""Single-camera capture queue; jobs are delivered at most once."""
import secrets
import threading
import time

from fastapi import HTTPException


class CameraState:
    def __init__(self, timeout=240):
        self.lock = threading.RLock()
        self.token = None
        self.seen = None
        self.job = None
        self.state = "idle"
        self.message = "Bereit."
        self.deadline = 0
        self.timeout = timeout
        self.preview_image = None
        self.preview_warning = ""
        self.model = "raspberrypi"

    def register(self, token, model="raspberrypi"):
        with self.lock:
            previous = self.token
            self.token = token
            self.model = model
            self.seen = None
            self.job = None
            self.preview_image = None
            self.state, self.message = "idle", "Bereit."
            return previous

    def snapshot(self):
        with self.lock:
            busy = self.state in {"queued", "capturing", "analyzing"}
            if busy and time.monotonic() >= self.deadline:
                self.state, self.message = "error", "Aufnahme abgelaufen. Erneut auslösen."
                busy = False
            return {"model": self.model, "name": "XIAO ESP32-S3 Sense" if self.model == "xiao_esp32s3" else "Raspberry-Pi-Kamera",
                    "manual_shutter": self.model != "xiao_esp32s3",
                    "online": self.seen is not None and time.monotonic() - self.seen < 15,
                    "busy": busy, "state": self.state, "message": self.message}

    def verify(self, token):
        if not self.token or token != self.token:
            raise HTTPException(403, "Dieses Gerät ist nicht die gekoppelte Kamera.")

    def heartbeat(self, token):
        with self.lock:
            self.verify(token)
            self.seen = time.monotonic()

    def away(self, token):
        with self.lock:
            if token == self.token:
                self.seen = None

    def disconnect(self, token):
        with self.lock:
            if token == self.token:
                self.token, self.seen = None, None
                self.preview_image = None
                self.state, self.message = "error", "Kamera getrennt."
            elif self.job and self.job["requester"] == token and self.snapshot()["busy"]:
                self.state, self.message = "error", "Aufnahme durch Entkoppeln abgebrochen."

    def request(self, requester, preview=False, settings=None):
        with self.lock:
            status = self.snapshot()
            if not status["online"]:
                raise HTTPException(503, "Kamera ist nicht verbunden.")
            if status["busy"]:
                raise HTTPException(409, "Eine Aufnahme läuft bereits.")
            self.job = {"id": secrets.token_urlsafe(16), "requester": requester, "preview": preview}
            self.job["settings"] = dict(settings or {"mode": "normal", "ev": 0, "shutter": 0})
            self.deadline = time.monotonic() + self.timeout
            self.state, self.message = "queued", "Aufnahme angefordert …"
            return {"id": self.job["id"]}

    def claim(self, token):
        with self.lock:
            self.heartbeat(token)
            self.snapshot()
            if self.state != "queued":
                return None
            self.state, self.message = "capturing", "Foto wird aufgenommen …"
            return {"id": self.job["id"]}

    def check_job(self, token, job_id, states):
        self.verify(token)
        self.snapshot()
        if not self.job or self.job["id"] != job_id or self.state not in states:
            raise HTTPException(409, "Aufnahme ist nicht mehr aktiv.")

    def begin(self, token, job_id):
        with self.lock:
            self.check_job(token, job_id, {"capturing"})
            preview = self.job["preview"]
            self.state = "analyzing"
            self.message = "Foto wird zur Anzeige vorbereitet …" if preview else "Foto wird am PC analysiert …"
            return preview

    def complete(self, token, job_id, publish):
        with self.lock:
            self.check_job(token, job_id, {"analyzing"})
            publish()
            self.state, self.message = "done", "Antwort ist da."

    def fail(self, token, job_id, message):
        with self.lock:
            self.check_job(token, job_id, {"capturing", "analyzing"})
            self.state, self.message = "error", message[:300]

    def complete_preview(self, token, job_id, image, warning=""):
        with self.lock:
            self.check_job(token, job_id, {"analyzing"})
            if not self.job["preview"]:
                raise HTTPException(409, "Keine Vorschau angefordert.")
            self.preview_image = (job_id, image)
            self.preview_warning = warning
            self.state, self.message = "done", "Foto angezeigt · ohne Analyse."

    def preview(self):
        with self.lock:
            return self.preview_image

    def job_options(self, token, job_id):
        with self.lock:
            self.check_job(token, job_id, {"capturing", "analyzing"})
            return dict(self.job["settings"])
