"""The latest successful answer, kept in memory for paired devices."""

from datetime import datetime, timezone
import threading


class ResultStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._version = 0
        self._answer = None
        self._updated_at = None

    def publish(self, answer):
        with self._lock:
            self._version += 1
            self._answer = answer
            self._updated_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self):
        with self._lock:
            return {
                "version": self._version,
                "answer": self._answer,
                "updated_at": self._updated_at,
            }
