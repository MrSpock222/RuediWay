"""Small MJPEG reader: one buffered frame, one camera process."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time


def exposure_options(settings, legacy=False):
    ev = max(-4.0, min(2.0, float(settings.get("ev", -1))))
    shutter = int(settings.get("shutter", 0))
    if shutter not in (0, 20000, 10000, 5000, 2000, 1000, 500):
        raise ValueError("Ungültige Belichtungszeit")
    if legacy:
        args = ["-ev", str(round(ev * 6))]
        if shutter:
            args += ["-ss", str(shutter), "-ISO", "100"]
        return args
    args = ["--metering", "centre", "--ev", str(ev), "--sharpness", "1.3"]
    if shutter:
        args += ["--shutter", str(shutter), "--gain", "1"]
    return args


class JpegFrames:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > 2 * 1024 * 1024:
                    self.buffer.clear()
                    raise RuntimeError("Ungültiger Kamerastream.")
                break
            frames.append(bytes(self.buffer[:end + 2]))
            del self.buffer[:end + 2]
        return frames


class Video:
    def __init__(self, settings, profile="preview"):
        executable = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
        if not executable:
            raise RuntimeError("Live benötigt rpicam-vid oder libcamera-vid auf dem Pi.")
        self.lock = threading.Lock()
        self.sequence = 0
        self.frame = None
        self.seen = time.monotonic()
        self.error = ""
        self.log = tempfile.TemporaryFile()
        width, height, fps, quality = (1280, 960, 3, 90) if profile == "scan" else (640, 480, 5, 65)
        command = [executable, "-n", "-t", "0", "--width", str(width), "--height", str(height),
                   "--framerate", str(fps), "--codec", "mjpeg", "--quality", str(quality), "--flush",
                   "--buffer-count", "3", "--no-raw", "-o", "-"] + exposure_options(settings)
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=self.log)
        except Exception:
            self.log.close()
            raise
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        parser = JpegFrames()
        try:
            while True:
                chunk = self.process.stdout.read1(65536)
                if not chunk:
                    break
                for frame in parser.feed(chunk):
                    with self.lock:
                        self.frame = frame
                        self.sequence += 1
                        self.seen = time.monotonic()
        except Exception as exc:
            self.error = str(exc)

    def latest(self):
        if self.error or self.process.poll() is not None or time.monotonic() - self.seen > 12:
            raise RuntimeError(self.error or "Kamera liefert kein Live-Bild. Kameraverbindung prüfen.")
        with self.lock:
            return self.sequence, self.frame

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.reader.join(timeout=3)
        self.process.stdout.close()
        self.log.seek(0)
        if self.process.returncode not in (0, -15):
            print(self.log.read(3000).decode(errors="replace"), flush=True)
        self.log.close()
