#!/usr/bin/env python3
"""Outbound-only Raspberry Pi camera client, using Python's standard library."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from video import Video, exposure_options

CONFIG = Path.home() / ".config/ruediway/camera.json"


class APIError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def request(config, path, method="GET", payload=None, photo=None, raw=None):
    headers = {"X-Ruediway-Token": config.get("token", "")}
    data = None
    if raw is not None:
        data = raw
        headers["Content-Type"] = "image/jpeg"
    elif photo is not None:
        boundary = uuid.uuid4().hex
        picture = photo.read_bytes()
        if len(picture) > 10 * 1024 * 1024:
            raise RuntimeError("Kamerabild ist größer als 10 MB.")
        data = ('--' + boundary + '\r\nContent-Disposition: form-data; name="image"; '
                'filename="camera.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()
        data += picture + ('\r\n--' + boundary + '--\r\n').encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(config["server"] + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=210 if photo else 8) as response:
            return json.loads(response.read(1024 * 1024))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096)).get("detail", "Serverfehler")
        except (ValueError, AttributeError):
            detail = "Serverfehler " + str(exc.code)
        raise APIError(exc.code, str(detail)) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("PC-Verbindung unterbrochen. Neuer Verbindungsversuch folgt automatisch.") from exc


def camera_command():
    for name in ("rpicam-still", "libcamera-still", "raspistill"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Keine Kamerasoftware gefunden (rpicam-still, libcamera-still, raspistill).")


def take_photo(path, settings=None):
    executable = camera_command()
    legacy = Path(executable).name == "raspistill"
    command = [executable, "-n", "-t", "3000", "--width", "2592",
               "--height", "1944", "-q", "95", "-o", str(path)]
    command += exposure_options(settings or {"ev": -1, "shutter": 0}, legacy=legacy)
    if not legacy:
        command += ["--no-raw", "--buffer-count", "2"]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=45, check=False)
    if result.returncode or not path.exists() or path.stat().st_size == 0:
        print(result.stderr.decode(errors="replace")[-2000:], flush=True)
        raise RuntimeError("Kameraaufnahme fehlgeschlagen. Kamera und Dienstprotokoll prüfen.")


def pair(server, code):
    server = server.rstrip("/")
    url = urllib.parse.urlsplit(server)
    if url.scheme != "http" or not url.hostname or url.path or url.query or url.fragment or url.username:
        raise RuntimeError("PC-Adresse als http://192.168.x.x:8000 angeben.")
    config = {"server": server}
    config["token"] = request(config, "/pair", "POST", {"code": code, "device": "camera"})["token"]
    CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=str(CONFIG.parent), delete=False) as target:
        temp = Path(target.name)
        os.chmod(str(temp), 0o600)
        json.dump(config, target)
    os.replace(str(temp), str(CONFIG))
    print("Kamera gekoppelt. Dienst starten oder neu starten.", flush=True)


def run():
    config = json.loads(CONFIG.read_text())
    camera_command()
    stop = threading.Event()
    expired = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    def heartbeat():
        while not stop.is_set():
            try:
                request(config, "/camera/heartbeat", "POST")
            except APIError as exc:
                if exc.code in (401, 403):
                    expired.set()
                    stop.set()
            except Exception:
                pass
            stop.wait(4)

    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    print("Kamera bereit. Warte auf Auslöser am PC oder auf der Uhr.", flush=True)
    video = None
    video_key = None
    control = {"session": None}
    next_poll = retry_after = 0
    uploaded = -1
    try:
        while not stop.is_set():
            job = None
            try:
                if time.monotonic() >= next_poll:
                    response = request(config, "/camera/next")
                    job = response["job"]
                    control = response.get("live", {"session": None})
                    next_poll = time.monotonic() + 1
                if job and not stop.is_set():
                    if video:
                        video.stop()
                        video = None
                    print("Aufnahme gestartet.", flush=True)
                    with tempfile.TemporaryDirectory(prefix="ruediway-") as directory:
                        photo = Path(directory) / "photo.jpg"
                        take_photo(photo, job.get("settings"))
                        if stop.is_set():
                            break
                        request(config, "/camera/jobs/" + job["id"] + "/image", "POST", photo=photo)
                    print("Aufnahme verarbeitet.", flush=True)
                    next_poll = 0
                    continue
                desired = (control["session"], control.get("revision", 0)) if control.get("session") else None
                if video and desired != video_key:
                    video.stop()
                    video = None
                if desired and video is None and time.monotonic() >= retry_after:
                    video = Video(control["settings"], control.get("profile", "preview"))
                    video_key = desired
                    uploaded = -1
                if video:
                    sequence, frame = video.latest()
                    if frame and sequence != uploaded:
                        request(config, "/camera/live/{}/{}/frame".format(*video_key), "POST", raw=frame)
                        uploaded = sequence
            except Exception as exc:
                print(str(exc), flush=True)
                if video:
                    video.stop()
                    video = None
                retry_after = time.monotonic() + 3
                next_poll = 0
                if isinstance(exc, APIError) and exc.code in (401, 403):
                    expired.set()
                    break
                if job:
                    try:
                        request(config, "/camera/jobs/" + job["id"] + "/error", "POST",
                                {"message": str(exc)[:300]})
                    except Exception:
                        pass
                elif control.get("session"):
                    try:
                        request(config, "/camera/live/" + control["session"] + "/error", "POST", {"message": str(exc)[:300]})
                    except Exception:
                        pass
            stop.wait(0.2 if control.get("session") else 0.5)
    finally:
        stop.set()
        if video:
            video.stop()
        worker.join(timeout=10)
        try:
            request(config, "/session/presence", "DELETE")
        except Exception:
            pass
    if expired.is_set():
        print("Kopplung abgelaufen. Neuen Code am PC erzeugen und erneut koppeln.", flush=True)
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pairing = commands.add_parser("pair")
    pairing.add_argument("--server", required=True)
    pairing.add_argument("--code", help="Ohne diese Option wird der Code abgefragt.")
    commands.add_parser("run")
    commands.add_parser("doctor")
    args = parser.parse_args()
    if args.command == "pair":
        pair(args.server, args.code or input("Code vom PC: ").strip())
    elif args.command == "doctor":
        print(camera_command())
    else:
        return run()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), flush=True)
        raise SystemExit(2)
