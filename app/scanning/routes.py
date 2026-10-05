import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field


class BoundaryRequest(BaseModel):
    frame_id: int
    points: list[tuple[float, float]] = Field(min_length=4, max_length=4)


def scan_router(manager, live, camera, local_only, busy, static_root):
    router = APIRouter(dependencies=[Depends(local_only)])

    def check_camera():
        if not camera.snapshot()["online"]:
            raise HTTPException(503, "Pi-Kamera nicht verbunden.")
        if busy() or camera.snapshot()["busy"]:
            raise HTTPException(409, "Eine Fotoaufnahme oder Analyse läuft noch.")

    def command(scan_id, name, payload=None):
        try:
            manager.command(scan_id, name, payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @router.get("/host/scanner")
    def scanner_page():
        return FileResponse(static_root / "scanner.html", headers={"Cache-Control": "no-store"})

    @router.post("/host/scan/start")
    def start():
        with manager.condition:
            check_camera()
            if manager.saving or (manager.engine and not manager.engine.saved):
                raise HTTPException(409, "Vorherigen Scan fortsetzen oder verwerfen.")
            session = live.start(profile="scan")
            try:
                return manager.start(session)
            except ValueError as exc:
                live.stop_session(session)
                raise HTTPException(409, str(exc)) from exc

    @router.get("/host/scan/status")
    def status():
        return JSONResponse(manager.snapshot(), headers={"Cache-Control": "no-store"})

    @router.post("/host/scan/{scan_id}/pause")
    def pause(scan_id: str):
        try:
            with manager.condition:
                manager.require(scan_id)
                manager.pause()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @router.post("/host/scan/{scan_id}/resume")
    def resume(scan_id: str):
        with manager.condition:
            check_camera()
            try:
                manager.require(scan_id)
                if not manager.engine.active or manager.saving:
                    raise ValueError("Dieser Scan kann nicht fortgesetzt werden.")
                session = live.start(profile="scan")
                manager.resume(scan_id, session)
            except ValueError as exc:
                if "session" in locals():
                    live.stop_session(session)
                raise HTTPException(409, str(exc)) from exc
            return manager.snapshot()

    @router.post("/host/scan/{scan_id}/freeze")
    def freeze(scan_id: str):
        return command(scan_id, "freeze")

    @router.post("/host/scan/{scan_id}/boundary")
    def boundary(scan_id: str, payload: BoundaryRequest):
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in payload.points):
            raise HTTPException(422, "Ecken müssen innerhalb des Bildes liegen.")
        return command(scan_id, "boundary", payload.model_dump())

    @router.post("/host/scan/{scan_id}/finish")
    def finish(scan_id: str):
        return command(scan_id, "finish")

    @router.post("/host/scan/{scan_id}/cancel")
    def cancel(scan_id: str):
        try:
            manager.cancel(scan_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @router.get("/host/scan/{scan_id}/view/{kind}")
    def image(scan_id: str, kind: str):
        if kind not in {"progress.jpg", "reference.jpg"}:
            raise HTTPException(404)
        data = manager.image(scan_id, kind)
        if not data:
            raise HTTPException(404, "Noch kein Scanbild verfügbar.")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/host/scans")
    def saved_scans():
        result = []
        if manager.root.exists():
            for folder in manager.root.iterdir():
                if not re.fullmatch(r"[a-f0-9]{32}", folder.name):
                    continue
                try:
                    data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
                    result.append({**{key: data[key] for key in ("id", "created", "state", "coverage")},
                                   "coverage_basis": data.get("coverage_basis", "page")})
                except (OSError, ValueError, KeyError):
                    continue
        return JSONResponse(sorted(result, key=lambda item: item["created"], reverse=True)[:30], headers={"Cache-Control": "no-store"})

    @router.get("/host/scans/{scan_id}/{name}")
    def download(scan_id: str, name: str):
        path = manager.saved_path(scan_id, name)
        if not path:
            raise HTTPException(404, "Scan-Datei nicht gefunden.")
        return FileResponse(path, filename=f"RuediWay-{scan_id[:8]}-{name}" if name == "scan.zip" else None,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    return router
