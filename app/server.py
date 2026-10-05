import asyncio
import io
from pathlib import Path
import secrets
import tempfile
import threading
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from app.backend import analyze_image, analyze_images
from app.pairing import CODE_LIFETIME, PairingState
from app.results import ResultStore
from app.camera import CameraState
from app.live import LiveView
from app.imaging import prepare_image
from app.scanning.service import ScanManager
from app.scanning.routes import scan_router
from app.scanning.analysis import ScanAnalysis

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000
BUSY = threading.Lock()
PAIRING = PairingState()
RESULTS = ResultStore()
CAMERA = CameraState()
LIVE = LiveView()
SCANNER = ScanManager(ROOT / "scans", stop_live=LIVE.stop_session,
                      live_current=lambda: LIVE.control()["session"])
SCAN_ANALYSIS = ScanAnalysis(SCANNER, BUSY, lambda: CAMERA.snapshot()["busy"],
                             lambda *args: analyze_images(*args), lambda answer: RESULTS.publish(answer),
                             ROOT / "prompts/analyze_scan.txt", ROOT / "captures")
app = FastAPI(title="RuediWay")


class PairRequest(BaseModel):
    code: str
    device: Literal["client", "camera"] = "client"
    camera_model: Literal["raspberrypi", "xiao_esp32s3"] = "raspberrypi"


class CameraError(BaseModel):
    message: str


class CameraSettings(BaseModel):
    mode: Literal["normal", "text", "bw"] = "text"
    ev: float = Field(default=-1.0, ge=-4, le=2, allow_inf_nan=False)
    shutter: Literal[0, 20000, 10000, 5000, 2000, 1000, 500] = 0


class PresenceRequest(BaseModel):
    token: str


def local_only(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Diese Seite ist nur am PC erreichbar.")
    if request.url.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "Host-Seite über die lokale PC-Adresse öffnen.")
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD"} and origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Aufnahme und Kopplung nur von der Host-Seite auslösen.")


def authorize(x_ruediway_token: str = Header(default="")):
    if not PAIRING.touch(x_ruediway_token):
        raise HTTPException(401, "Verbindung abgelaufen. Handy erneut koppeln.")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/host", dependencies=[Depends(local_only)])
def host():
    return FileResponse(ROOT / "static" / "host.html", headers={"Cache-Control": "no-store"})


@app.post("/host/code", dependencies=[Depends(local_only)])
def generate_code():
    return JSONResponse(
        {"code": PAIRING.generate(), "expires_in": CODE_LIFETIME},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/host/status", dependencies=[Depends(local_only)])
def host_status():
    preview = CAMERA.preview()
    return JSONResponse({**PAIRING.status(), "camera": CAMERA.snapshot(), "result": RESULTS.snapshot(),
                         "preview_id": preview[0] if preview else None,
                         "preview_warning": CAMERA.preview_warning if preview else "",
                         "settings": LIVE.options(), "live": LIVE.status(), "analysis_busy": BUSY.locked(),
                         "scan_analysis": SCAN_ANALYSIS.snapshot()}, headers={"Cache-Control": "no-store"})


@app.get("/host/preview/{preview_id}", dependencies=[Depends(local_only)])
def host_preview_image(preview_id: str):
    preview = CAMERA.preview()
    if not preview or preview[0] != preview_id:
        raise HTTPException(404, "Kein aktuelles Vorschaubild vorhanden.")
    return Response(preview[1], media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.post("/pair")
def pair(payload: PairRequest):
    if len(payload.code) != 6 or not payload.code.isascii() or not payload.code.isdigit():
        raise HTTPException(400, "Bitte den sechsstelligen Code eingeben.")
    try:
        token = PAIRING.pair(payload.code, RESULTS.snapshot()["version"])
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    if payload.device == "camera":
        LIVE.stop()
        previous = CAMERA.register(token, payload.camera_model)
        if payload.camera_model == "xiao_esp32s3":
            LIVE.configure({**LIVE.options(), "shutter": 0, "ev": max(-2, LIVE.options()["ev"])})
        if previous:
            PAIRING.revoke(previous)
    return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})


@app.get("/session", dependencies=[Depends(authorize)])
def session():
    return {"connected": True}


@app.get("/watch/result", dependencies=[Depends(authorize)])
def watch_result(x_ruediway_token: str = Header(default="")):
    result = RESULTS.snapshot()
    result["camera"] = CAMERA.snapshot()
    if result["version"] <= PAIRING.result_baseline(x_ruediway_token):
        result["answer"] = None
        result["updated_at"] = None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@app.delete("/session", dependencies=[Depends(authorize)])
def disconnect(x_ruediway_token: str = Header(default="")):
    if x_ruediway_token == CAMERA.token:
        LIVE.stop()
    CAMERA.disconnect(x_ruediway_token)
    PAIRING.revoke(x_ruediway_token)
    return {"connected": False}


@app.delete("/session/presence", dependencies=[Depends(authorize)])
def away(x_ruediway_token: str = Header(default="")):
    CAMERA.away(x_ruediway_token)
    PAIRING.mark_away(x_ruediway_token)
    return {"connected": False}


@app.post("/session/away")
def away_beacon(payload: PresenceRequest):
    if not PAIRING.valid(payload.token):
        raise HTTPException(401, "Verbindung abgelaufen. Handy erneut koppeln.")
    PAIRING.mark_away(payload.token)
    return {"connected": False}


@app.websocket("/session/live")
async def live(websocket: WebSocket):
    await websocket.accept()
    token = None
    connection_id = secrets.token_urlsafe(12)
    try:
        token = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if not PAIRING.attach(token, connection_id):
            await websocket.close(code=1008)
            return
        await websocket.send_text("ready")
        while True:
            message = await websocket.receive_text()
            if message != "ping" or not PAIRING.touch(token):
                await websocket.close(code=1008)
                return
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if token is not None:
            PAIRING.detach(token, connection_id)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze", dependencies=[Depends(authorize)])
def analyze(image: UploadFile):
    if SCANNER.is_active():
        image.file.close()
        raise HTTPException(409, "Seitenscan läuft. Hier werden gerade nur Bilder erfasst.")
    return process_image(image, RESULTS.publish)


def normalize_upload(image, mode="normal"):
    data = image.file.read(MAX_BYTES + 1)
    return prepare_image(data, mode)[0]


def process_image(image, publish, mode="normal"):
    with SCANNER.condition:
        if SCANNER.is_active():
            image.file.close()
            raise HTTPException(409, "Seitenscan läuft. Erst pausieren oder abschließen.")
        if not BUSY.acquire(blocking=False):
            image.file.close()
            raise HTTPException(429, "Eine Analyse läuft bereits. Bitte kurz warten.")
    try:
        data = normalize_upload(image, mode)
        captures = ROOT / "captures"
        captures.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=captures) as folder:
            photo = Path(folder) / "photo.jpg"
            photo.write_bytes(data)
            try:
                answer = analyze_image(photo, (ROOT / "prompts/analyze.txt").read_text(encoding="utf-8"), Path(folder) / "answer.txt")
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)) from exc
            publish(answer)
            return {"answer": answer}
    finally:
        image.file.close()
        BUSY.release()


def request_capture(requester, preview=False):
    with SCANNER.condition:
        if SCANNER.is_active():
            raise HTTPException(409, "Seitenscan läuft. Erst pausieren oder abschließen.")
        if BUSY.locked():
            raise HTTPException(409, "Eine Analyse läuft bereits.")
        return CAMERA.request(requester, preview=preview, settings=LIVE.options())


@app.post("/host/capture", dependencies=[Depends(local_only)])
def host_capture():
    return request_capture("host")


@app.post("/host/preview", dependencies=[Depends(local_only)])
def host_preview_capture():
    return request_capture("host", preview=True)


@app.post("/capture", dependencies=[Depends(authorize)])
def capture(x_ruediway_token: str = Header(default="")):
    return request_capture(x_ruediway_token)


@app.get("/camera/next", dependencies=[Depends(authorize)])
def camera_next(x_ruediway_token: str = Header(default="")):
    job = CAMERA.claim(x_ruediway_token)
    if job:
        job["settings"] = CAMERA.job_options(x_ruediway_token, job["id"])
    return JSONResponse({"job": job, "live": LIVE.control()}, headers={"Cache-Control": "no-store"})


@app.post("/camera/heartbeat", dependencies=[Depends(authorize)])
def camera_heartbeat(x_ruediway_token: str = Header(default="")):
    CAMERA.heartbeat(x_ruediway_token)
    return {"connected": True}


@app.post("/camera/jobs/{job_id}/error", dependencies=[Depends(authorize)])
def camera_error(job_id: str, payload: CameraError, x_ruediway_token: str = Header(default="")):
    CAMERA.fail(x_ruediway_token, job_id, payload.message)
    return {"ok": True}


@app.post("/camera/jobs/{job_id}/image", dependencies=[Depends(authorize)])
def camera_image(job_id: str, image: UploadFile, x_ruediway_token: str = Header(default="")):
    begun = False
    try:
        preview = CAMERA.begin(x_ruediway_token, job_id)
        begun = True
        mode = CAMERA.job_options(x_ruediway_token, job_id)["mode"]
        return process_camera_upload(job_id, image, x_ruediway_token, preview, mode)
    except HTTPException as exc:
        try:
            if begun:
                CAMERA.fail(x_ruediway_token, job_id, str(exc.detail))
        except HTTPException:
            pass
        raise
    finally:
        image.file.close()


def process_camera_upload(job_id, image, token, preview, mode):
    if preview:
        picture, warning = prepare_image(image.file.read(MAX_BYTES + 1), mode)
        CAMERA.complete_preview(token, job_id, picture, warning)
        return {"preview": True}
    return process_image(image, lambda answer: CAMERA.complete(
        token, job_id, lambda: RESULTS.publish(answer)), mode=mode)


def finish_camera_jpeg(job_id, raw, token, preview, mode):
    image = UploadFile(file=io.BytesIO(raw), filename="esp32.jpg")
    try:
        process_camera_upload(job_id, image, token, preview, mode)
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else "Fotoverarbeitung fehlgeschlagen."
        try:
            CAMERA.fail(token, job_id, message)
        except HTTPException:
            pass  # replaced camera, expired job or disconnected requester
    finally:
        image.file.close()


@app.post("/camera/jobs/{job_id}/jpeg", dependencies=[Depends(authorize)])
async def camera_jpeg(job_id: str, request: Request, background: BackgroundTasks,
                      x_ruediway_token: str = Header(default="")):
    # A raw JPEG avoids a second full-frame allocation on the ESP32. Reserve
    # before reading so retries/parallel uploads cannot enqueue the job twice.
    preview = CAMERA.begin(x_ruediway_token, job_id)
    try:
        mode = CAMERA.job_options(x_ruediway_token, job_id)["mode"]
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, "Kamerabild zu groß.")
        if not data:
            raise HTTPException(400, "Leeres Kamerabild.")
        background.add_task(finish_camera_jpeg, job_id, bytes(data), x_ruediway_token, preview, mode)
        return JSONResponse({"accepted": True, "id": job_id}, status_code=202)
    except Exception:
        try:
            CAMERA.fail(x_ruediway_token, job_id, "JPEG-Upload fehlgeschlagen. Erneut aufnehmen.")
        except HTTPException:
            pass
        raise


@app.post("/host/camera/settings", dependencies=[Depends(local_only)])
def camera_settings(payload: CameraSettings):
    if CAMERA.model == "xiao_esp32s3" and (payload.shutter != 0 or payload.ev < -2):
        raise HTTPException(422, "ESP32: automatische Belichtungszeit und Belichtungsstufe −2 bis +2 verwenden.")
    LIVE.configure(payload.model_dump())
    return LIVE.options()


@app.post("/host/live/start", dependencies=[Depends(local_only)])
def live_start():
    if SCANNER.is_active():
        raise HTTPException(409, "Seitenscan läuft. Die Live-Vorschau ist im Scanner verfügbar.")
    if not CAMERA.snapshot()["online"]:
        raise HTTPException(503, "Kamera ist nicht verbunden.")
    return {"session": LIVE.start()}


@app.post("/host/live/{session}/keepalive", dependencies=[Depends(local_only)])
def live_keepalive(session: str):
    LIVE.keepalive(session)
    return {"ok": True}


@app.post("/host/live/{session}/stop", dependencies=[Depends(local_only)])
def live_stop(session: str):
    LIVE.stop_session(session)
    return {"ok": True}


@app.get("/host/live/{session}/stream", dependencies=[Depends(local_only)])
async def live_stream(session: str, request: Request):
    LIVE.read(session)

    async def frames():
        previous = -1
        while not await request.is_disconnected():
            try:
                sequence, frame = LIVE.read(session)
            except HTTPException:
                break
            if frame is not None and sequence != previous:
                previous = sequence
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(0.05)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.post("/camera/live/{session}/{revision}/frame", dependencies=[Depends(authorize)])
async def live_upload(session: str, revision: int, request: Request, x_ruediway_token: str = Header(default=""),
                      x_ruediway_preview_only: str = Header(default="0", pattern="^[01]$")):
    CAMERA.heartbeat(x_ruediway_token)
    control = LIVE.control()
    if session != control["session"] or revision != control["revision"]:
        raise HTTPException(409, "Live-Vorschau nicht mehr aktuell.")
    data = bytearray()
    try:
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 2 * 1024 * 1024:
                raise HTTPException(413, "Live-Bild zu groß.")
    except ClientDisconnect as exc:
        raise HTTPException(400, "Bildübertragung abgebrochen.") from exc
    frame, warning = await asyncio.to_thread(prepare_image, bytes(data), control["settings"]["mode"], 800)
    LIVE.publish(session, revision, frame, warning)
    # A reduced preview must never masquerade as original scan pixels.
    if x_ruediway_preview_only != "1":
        SCANNER.submit(session, bytes(data))
    return {"ok": True}


@app.post("/camera/live/{session}/error", dependencies=[Depends(authorize)])
def live_error(session: str, payload: CameraError, x_ruediway_token: str = Header(default="")):
    CAMERA.heartbeat(x_ruediway_token)
    LIVE.failure(session, payload.message)
    return {"ok": True}


@app.post("/host/scans/{scan_id}/analysis", dependencies=[Depends(local_only)])
def start_scan_analysis(scan_id: str):
    return JSONResponse(SCAN_ANALYSIS.start(scan_id), headers={"Cache-Control": "no-store"})


@app.get("/host/scans/{scan_id}/analysis", dependencies=[Depends(local_only)])
def scan_analysis_status(scan_id: str):
    return JSONResponse(SCAN_ANALYSIS.snapshot(scan_id), headers={"Cache-Control": "no-store"})


app.include_router(scan_router(SCANNER, LIVE, CAMERA, local_only, BUSY.locked, ROOT / "static"))


@app.on_event("shutdown")
def stop_scanner():
    SCANNER.close()
