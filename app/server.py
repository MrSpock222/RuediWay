import asyncio
import io
from pathlib import Path
import secrets
import tempfile
import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from app.backend import analyze_image
from app.pairing import CODE_LIFETIME, PairingState
from app.results import ResultStore

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000
BUSY = threading.Lock()
PAIRING = PairingState()
RESULTS = ResultStore()
app = FastAPI(title="RuediWay")


class PairRequest(BaseModel):
    code: str


class PresenceRequest(BaseModel):
    token: str


def local_only(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Diese Seite ist nur am PC erreichbar.")


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
    return JSONResponse(PAIRING.status(), headers={"Cache-Control": "no-store"})


@app.post("/pair")
def pair(payload: PairRequest):
    if len(payload.code) != 6 or not payload.code.isascii() or not payload.code.isdigit():
        raise HTTPException(400, "Bitte den sechsstelligen Code eingeben.")
    try:
        token = PAIRING.pair(payload.code, RESULTS.snapshot()["version"])
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})


@app.get("/session", dependencies=[Depends(authorize)])
def session():
    return {"connected": True}


@app.get("/watch/result", dependencies=[Depends(authorize)])
def watch_result(x_ruediway_token: str = Header(default="")):
    result = RESULTS.snapshot()
    if result["version"] <= PAIRING.result_baseline(x_ruediway_token):
        result["answer"] = None
        result["updated_at"] = None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@app.delete("/session", dependencies=[Depends(authorize)])
def disconnect(x_ruediway_token: str = Header(default="")):
    PAIRING.revoke(x_ruediway_token)
    return {"connected": False}


@app.delete("/session/presence", dependencies=[Depends(authorize)])
def away(x_ruediway_token: str = Header(default="")):
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
    if not BUSY.acquire(blocking=False):
        raise HTTPException(429, "Eine Analyse läuft bereits. Bitte kurz warten.")
    try:
        data = image.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Bild darf maximal 10 MB groß sein.")
        captures = ROOT / "captures"
        captures.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=captures) as folder:
            photo = Path(folder) / "photo.jpg"
            try:
                with Image.open(io.BytesIO(data)) as source:
                    if source.width * source.height > 16_000_000:
                        raise HTTPException(413, "Bild darf maximal 16 Megapixel haben.")
                    normalized = ImageOps.exif_transpose(source).convert("RGB")
                    normalized.thumbnail((2048, 2048))
                    normalized.save(photo, "JPEG", quality=90)
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise HTTPException(400, "Ungültiges oder zu großes Bild. JPEG oder PNG verwenden.") from exc
            try:
                answer = analyze_image(photo, (ROOT / "prompts/analyze.txt").read_text(encoding="utf-8"), Path(folder) / "answer.txt")
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)) from exc
            RESULTS.publish(answer)
            return {"answer": answer}
    finally:
        image.file.close()
        BUSY.release()
