"""In-memory, short-lived pairing codes and browser sessions."""

import hmac
import secrets
import threading
import time

CODE_LIFETIME = 5 * 60
SESSION_LIFETIME = 24 * 60 * 60
PRESENCE_TIMEOUT = 15
MAX_ATTEMPTS = 5


class PairingState:
    def __init__(self):
        self._lock = threading.Lock()
        self._code = None
        self._code_expires = 0.0
        self._attempts = 0
        self._sessions = {}
        self._connections = {}
        self._result_baselines = {}

    def generate(self):
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._code_expires = time.monotonic() + CODE_LIFETIME
            self._attempts = 0
            return self._code

    def pair(self, code, result_version=0):
        with self._lock:
            now = time.monotonic()
            if self._code is None or now >= self._code_expires:
                self._code = None
                raise ValueError("Code abgelaufen. Am PC einen neuen erzeugen.")
            if not hmac.compare_digest(code, self._code):
                self._attempts += 1
                if self._attempts >= MAX_ATTEMPTS:
                    self._code = None
                    raise ValueError("Zu viele Versuche. Am PC einen neuen Code erzeugen.")
                raise ValueError("Code stimmt nicht.")
            self._code = None
            token = secrets.token_urlsafe(32)
            self._sessions[token] = (now + SESSION_LIFETIME, now)
            self._result_baselines[token] = result_version
            return token

    def result_baseline(self, token):
        with self._lock:
            return self._result_baselines.get(token, 0)

    def _forget(self, token):
        self._sessions.pop(token, None)
        self._connections.pop(token, None)
        self._result_baselines.pop(token, None)

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            expires, _ = self._sessions.get(token, (0, None))
            if time.monotonic() >= expires:
                self._forget(token)
                return False
            return True

    def touch(self, token):
        if not token:
            return False
        with self._lock:
            now = time.monotonic()
            expires, _ = self._sessions.get(token, (0, None))
            if now >= expires:
                self._forget(token)
                return False
            self._sessions[token] = (expires, now)
            return True

    def mark_away(self, token):
        with self._lock:
            if token in self._sessions and not self._connections.get(token):
                expires, _ = self._sessions[token]
                self._sessions[token] = (expires, None)

    def attach(self, token, connection_id):
        if not token:
            return False
        with self._lock:
            now = time.monotonic()
            expires, _ = self._sessions.get(token, (0, None))
            if now >= expires:
                self._forget(token)
                return False
            self._connections.setdefault(token, set()).add(connection_id)
            self._sessions[token] = (expires, now)
            return True

    def detach(self, token, connection_id):
        with self._lock:
            connections = self._connections.get(token)
            if connections is None:
                return
            connections.discard(connection_id)
            if not connections:
                self._connections.pop(token, None)
                if token in self._sessions:
                    expires, _ = self._sessions[token]
                    self._sessions[token] = (expires, None)

    def status(self):
        with self._lock:
            now = time.monotonic()
            for token, (expires, _) in list(self._sessions.items()):
                if now >= expires:
                    self._forget(token)
            if self._code is not None and now >= self._code_expires:
                self._code = None
            connected = sum(
                seen is not None and now - seen < PRESENCE_TIMEOUT
                for _, seen in self._sessions.values()
            )
            return {"connected": connected > 0, "devices": connected, "code_active": self._code is not None}

    def revoke(self, token):
        with self._lock:
            self._forget(token)
