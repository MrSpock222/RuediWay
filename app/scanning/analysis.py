"""Explicit, asynchronous analysis of saved scans, using the shared CLI backend."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import tempfile
import threading

from fastapi import HTTPException


def now():
    return datetime.now(timezone.utc).isoformat()


class ScanAnalysis:
    def __init__(self, scanner, busy, camera_busy, analyze, publish, prompt, captures):
        self.scanner, self.busy, self.camera_busy = scanner, busy, camera_busy
        self.analyze, self.publish = analyze, publish
        self.prompt, self.captures = Path(prompt), Path(captures)
        self.lock = threading.RLock()
        self.current = None
        self.thread = None

    def folder(self, scan_id):
        metadata = self.scanner.saved_path(scan_id, "metadata.json")
        if metadata is None:
            raise HTTPException(404, "Gespeicherter Scan nicht gefunden.")
        folder = metadata.parent.resolve()
        if folder.parent != self.scanner.root.resolve():
            raise HTTPException(404, "Ungültiger Scanpfad.")
        return folder

    def snapshot(self, scan_id=None):
        with self.lock:
            if scan_id is None:
                return dict(self.current) if self.current else {"state": "idle", "scan_id": None}
            folder = self.folder(scan_id)
            if self.current and self.current["scan_id"] == scan_id:
                return dict(self.current)
            try:
                saved = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {"state": "idle", "scan_id": scan_id, "answer": None}
            except (OSError, ValueError):
                return {"state": "error", "scan_id": scan_id, "answer": None,
                        "message": "Gespeicherte Analyse nicht lesbar. Erneut analysieren."}
            if saved.get("state") == "running":
                return {"state": "error", "scan_id": scan_id, "answer": None,
                        "message": "Analyse wurde durch einen Host-Neustart unterbrochen. Erneut starten."}
            return saved

    def persist(self, folder, status):
        pending = folder / ".analysis.pending"
        pending.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(folder / "analysis.json")

    def start(self, scan_id):
        # Same lock order as photo capture/start/resume: reserve the global AI
        # slot before releasing the scanner lock, preventing scan/AI races.
        with self.scanner.condition, self.lock:
            folder = self.folder(scan_id)
            previous = self.snapshot(scan_id)
            if previous["state"] in {"running", "complete"}:
                return previous  # double clicks/reloads never spend another request
            if self.scanner.is_active():
                raise HTTPException(409, "Seitenscan läuft. Erst pausieren oder abschließen.")
            if self.camera_busy():
                raise HTTPException(409, "Eine Kameraaufnahme läuft. Bitte kurz warten.")
            if not self.busy.acquire(blocking=False):
                raise HTTPException(409, "Eine Analyse läuft bereits. Bitte kurz warten.")
            try:
                metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
                if metadata.get("state") not in {"complete", "partial"} or not metadata.get("saved"):
                    raise HTTPException(409, "Scan zuerst fertig speichern.")
                for name in ("page.png", "document.png"):
                    path = folder / name
                    if not path.is_file() or path.resolve().parent != folder:
                        raise HTTPException(409, "Gesamtbild oder Textansicht fehlt.")
                status = {"state": "running", "scan_id": scan_id, "answer": None,
                          "message": "Scan wird analysiert …", "started_at": now(),
                          "model": "gpt-6-luna", "reasoning": "medium",
                          "inputs": ["page.png", "document.png"],
                          "scan_state": metadata["state"], "coverage": metadata.get("coverage"),
                          "coverage_basis": metadata.get("coverage_basis", "page")}
                self.persist(folder, status)
                self.current = status
                self.thread = threading.Thread(target=self._run, args=(folder, dict(status)),
                                               name="ruediway-scan-analysis", daemon=True)
                self.thread.start()
                return dict(status)
            except Exception:
                self.busy.release()
                # If thread creation failed, allow an explicit retry.
                if self.current and self.current["scan_id"] == scan_id:
                    self.current = None
                raise

    def _run(self, folder, status):
        try:
            partial = status["scan_state"] == "partial"
            context = ("Teilscan: Es können Fragen oder Textteile fehlen." if partial else
                       "Vollständig erfasster Scan laut Bildqualitätsschätzung; keine Lesbarkeitsgarantie.")
            prompt = self.prompt.read_text(encoding="utf-8") + "\n\nScanstatus: " + context
            self.captures.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="scan-analysis-", dir=self.captures) as temporary:
                temporary = Path(temporary)
                images = []
                for name in status["inputs"]:
                    target = temporary / name
                    shutil.copyfile(folder / name, target)
                    images.append(target)
                answer = self.analyze(images, prompt, temporary / "answer.txt")
            status.update(state="complete", answer=answer, finished_at=now(),
                          message="Analyse fertig. Antwort an Host und gekoppelte Uhr übergeben.")
            # Save first: a publication must never claim success for a lost answer.
            self.persist(folder, status)
            self.publish(answer)
        except Exception as exc:
            logging.getLogger(__name__).exception("Scan analysis failed")
            status.update(state="error", answer=None, finished_at=now(),
                          message=str(exc) if isinstance(exc, RuntimeError) else
                          "Scan-Analyse fehlgeschlagen. Bitte erneut versuchen; Details im Host-Protokoll.")
            try:
                self.persist(folder, status)
            except OSError:
                logging.getLogger(__name__).exception("Could not save scan analysis status")
        finally:
            with self.lock:
                self.current = status
            self.busy.release()
