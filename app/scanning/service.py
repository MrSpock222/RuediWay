"""Single worker with a latest-frame mailbox. HTTP never waits for stitching."""
import re
import threading
import time
from collections import deque
from pathlib import Path

from .engine import ScanEngine, encode


class ScanManager:
    def __init__(self, root, stop_live=lambda _: None, live_current=lambda: None):
        self.root = Path(root)
        self.stop_live = stop_live
        self.live_current = live_current
        self.condition = threading.Condition(threading.RLock())
        self.engine = None
        self.session = None
        self.pending = None
        self.action = None
        self.paused = False
        self.saving = False
        self.closed = False
        self.thread = None
        self.status = {"state": "idle", "message": "Noch kein Scan gestartet.", "id": None}
        self.images = {}
        self.last_seen = 0
        self.last_submit = 0
        self.view_revision = 0
        self.dropped = 0
        self.input_frames = 0
        self.processed_frames = 0
        self.processing_ms = 0.0
        self.queue_ms = 0.0
        self.completed_at = deque(maxlen=30)

    def is_active(self):
        with self.condition:
            return self.saving or (self.engine is not None and
                (self.engine.active or (self.engine.state == "complete" and not self.engine.saved)) and not self.paused)

    def start(self, session):
        with self.condition:
            if self.saving or (self.engine and not self.engine.saved):
                raise ValueError("Laufenden Scan fortsetzen, speichern oder verwerfen.")
            self.engine = ScanEngine()
            self.session = session
            self.pending = self.action = None
            self.paused = False
            self.last_seen = time.monotonic()
            self.last_submit = 0
            self.dropped = 0
            self.input_frames = self.processed_frames = 0
            self.processing_ms = self.queue_ms = 0.0
            self.completed_at.clear()
            self.images = {}
            self.status = self.engine.snapshot()
            if self.thread is None:
                self.thread = threading.Thread(target=self._worker, name="ruediway-scan", daemon=True)
                self.thread.start()
            self.condition.notify_all()
            return self.snapshot()

    def submit(self, session, data):
        if len(data) > 2 * 1024 * 1024:
            return
        with self.condition:
            if session != self.session or not self.is_active() or self.saving:
                return
            self.last_seen = time.monotonic()
            self.input_frames += 1
            self.last_submit = self.last_seen
            if self.pending is not None:
                self.dropped += 1
            self.pending = self.engine, bytes(data), self.last_seen
            self.condition.notify_all()

    def pause(self):
        with self.condition:
            self.paused = True
            self.pending = None
            session = self.session
        if session:
            self.stop_live(session)

    def resume(self, scan_id, session):
        with self.condition:
            self.require(scan_id)
            if not self.engine.active or self.saving:
                raise ValueError("Dieser Scan kann nicht fortgesetzt werden.")
            self.session = session
            self.paused = False
            self.pending = None
            self.last_seen = time.monotonic()
            self.last_submit = 0
            self.condition.notify_all()

    def require(self, scan_id):
        if not self.engine or self.engine.id != scan_id:
            raise ValueError("Scan ist nicht mehr aktuell.")

    def command(self, scan_id, command, payload=None):
        with self.condition:
            self.require(scan_id)
            if self.action or self.saving or self.engine.saved:
                raise ValueError("Scan wird bereits verarbeitet oder ist gespeichert.")
            self.action = self.engine, command, payload
            self.condition.notify_all()

    def cancel(self, scan_id):
        with self.condition:
            self.require(scan_id)
            if self.saving:
                raise ValueError("Scan wird gerade gespeichert.")
            session = self.session
            self.engine = None
            self.pending = self.action = None
            self.images = {}
            self.status = {"state": "cancelled", "id": None, "message": "Ungespeicherter Scan verworfen."}
        self.stop_live(session)

    def snapshot(self):
        with self.condition:
            result = dict(self.status)
            if self.saving:
                result["state"], result["message"] = "saving", "Scan wird lokal gespeichert …"
            elif self.paused and self.engine and self.engine.active:
                result["state"] = "paused"
                result["message"] = "Scan pausiert. Fortsetzen und einen bekannten Bereich zeigen."
            elif self.is_active() and time.monotonic() - self.last_seen > 8:
                result["message"] = "Keine aktuellen Kamerabilder. Verbindung prüfen; der Scan bleibt erhalten."
            result.update({"view_revision": self.view_revision, "dropped_frames": self.dropped,
                           "live_session": self.session if self.is_active() else None})
            fps = 0.0
            if len(self.completed_at) > 1 and time.monotonic() - self.completed_at[-1] < 5:
                fps = (len(self.completed_at) - 1) / max(.001, self.completed_at[-1] - self.completed_at[0])
            result["pipeline"] = {"received": self.input_frames, "processed": self.processed_frames,
                                  "superseded": self.dropped, "processing_ms": round(self.processing_ms, 1),
                                  "queue_ms": round(self.queue_ms, 1), "fps": round(fps, 2)}
            return result

    def image(self, scan_id, kind):
        with self.condition:
            if self.status.get("id") != scan_id:
                return None
            return self.images.get(kind)

    def _publish(self, engine):
        preview = engine.preview()
        reference = encode(engine.frozen) if engine.state == "mark_page" else None
        status = engine.snapshot()
        with self.condition:
            if engine is not self.engine:
                return
            self.status = status
            if preview:
                self.images["progress.jpg"] = preview
            if reference:
                self.images["reference.jpg"] = reference
            self.view_revision += 1

    def _save(self, engine):
        with self.condition:
            if engine is not self.engine:
                return
            self.saving = True
            self.pending = None
            session = self.session
        self.stop_live(session)
        try:
            engine.finish(self.root)
        finally:
            with self.condition:
                self.saving = False

    def _worker(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.action or self.pending, timeout=1)
                if self.closed:
                    return
                if self.is_active() and self.live_current() != self.session:
                    self.paused = True
                    self.pending = None
                action, pending = self.action, self.pending
                if action:
                    self.action = None
                elif pending:
                    self.pending = None
                else:
                    continue
            engine = action[0] if action else pending[0]
            started = time.monotonic()
            try:
                if action:
                    _, command, payload = action
                    if command == "freeze":
                        engine.freeze()
                    elif command == "boundary":
                        engine.set_boundary(payload["points"], payload["frame_id"])
                    elif command == "finish":
                        self._save(engine)
                else:
                    engine.feed(pending[1])
                if engine.state == "complete" and not engine.saved:
                    self._save(engine)
                self._publish(engine)
            except ValueError as exc:
                engine.message = str(exc)
                self._publish(engine)
            except Exception:
                # Keep errors visible and avoid publishing a false completion.
                import logging
                logging.getLogger(__name__).exception("Scan processing failed")
                engine.state = "error"
                engine.message = "Scanverarbeitung fehlgeschlagen. Teilscan speichern oder neu beginnen; Details im Serverprotokoll."
                self.stop_live(self.session)
                self._publish(engine)
            finally:
                if not action:
                    completed = time.monotonic()
                    with self.condition:
                        if engine is self.engine:
                            self.processed_frames += 1
                            self.processing_ms = (completed - started) * 1000
                            self.queue_ms = max(0, (started - pending[2]) * 1000)
                            self.completed_at.append(completed)

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = self.action = None
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=10)

    def saved_path(self, scan_id, name):
        if not re.fullmatch(r"[a-f0-9]{32}", scan_id) or name not in {"page.png", "document.png", "coverage.jpg", "metadata.json", "scan.zip"}:
            return None
        path = self.root / scan_id / name
        return path if path.is_file() else None
