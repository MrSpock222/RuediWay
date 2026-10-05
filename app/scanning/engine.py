"""One planar page, bounded keyframes, conservative coverage and no content AI."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid
import zipfile

import cv2 as cv
import numpy as np
from PIL import Image, UnidentifiedImageError

from .geometry import Registrar, agreement, corners, detect_page, ordered_quad, page_transform, project, valid_page_quad
from .quality import assess, quality_map
from .compositing import normalize_paper, merge_regions, supported_pixels
from .text_regions import project_text, region_quality


def encode(image, extension=".jpg"):
    ok, result = cv.imencode(extension, image, [cv.IMWRITE_JPEG_QUALITY, 92] if extension == ".jpg" else [])
    if not ok:
        raise RuntimeError("Scan-Bild konnte nicht erzeugt werden.")
    return result.tobytes()


class ScanEngine:
    MAX_SOURCES = 100

    def __init__(self, scan_id=None, long_side=2800):
        self.id = scan_id or uuid.uuid4().hex
        self.created = datetime.now(timezone.utc).isoformat()
        self.state = "seeking_page"
        self.message = "Zuerst die ganze Seite mit allen vier Ecken zeigen, möglichst gerade von oben."
        self.registrar = Registrar()
        self.long_side = long_side
        self.page_size = None
        self.mosaic = self.seen = self.scores = None
        self.expected_ink = None
        self.text_target = None
        self.text_regions = []
        self.anchor_image = None
        self.keyframes = []
        self.sources = []
        self.hashes = set()
        self.latest = self.frozen = None
        self.latest_id = self.frozen_id = 0
        self.candidate = None
        self.candidate_count = 0
        self.frame_count = self.rejected = self.accepted = self.lost = 0
        self.reason_counts = {}
        self.last_quality = {}
        self.detail_diagnostics = {}
        self.quality_pixel_coverage = 0.0
        self.last_polygon = None
        self.boundary_source = None
        self.full_confirmations = 0
        self.coverage = self.seen_coverage = 0.0
        self.grid = []
        self.revision = 0
        self.saved = False

    @property
    def active(self):
        return self.state in {"seeking_page", "mark_page", "scanning"}

    def reject(self, reason, key):
        self.rejected += 1
        self.reason_counts[key] = self.reason_counts.get(key, 0) + 1
        self.message = reason
        self.full_confirmations = 0

    def freeze(self):
        if self.page_size:
            raise ValueError("Für einen anderen Seitenrahmen bitte einen neuen Scan beginnen.")
        if self.latest is None:
            raise ValueError("Noch kein Kamerabild angekommen.")
        self.frozen = self.latest.copy()
        self.frozen_id = self.latest_id
        self.state = "mark_page"
        self.message = "Die vier Seitenecken im eingefrorenen Bild anklicken."
        self.revision += 1

    def set_boundary(self, points, frame_id):
        if self.state != "mark_page" or frame_id != self.frozen_id:
            raise ValueError("Das markierte Bild ist nicht mehr aktuell.")
        h, w = self.frozen.shape[:2]
        quad = ordered_quad(np.asarray(points, np.float32) * [w, h])
        if not valid_page_quad(quad, self.frozen.shape):
            raise ValueError("Alle vier Ecken einer ausreichend großen Seite innerhalb des Bildes markieren.")
        quality = assess(cv.cvtColor(self.frozen, cv.COLOR_BGR2GRAY), overview=True)
        if not quality["ok"]:
            raise ValueError("Übersichtsbild zu unscharf/dunkel. Neuen Scan beginnen und Abstand/Licht anpassen.")
        self.initialize(self.frozen, quad, "manual")

    def initialize(self, image, quad, source):
        gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
        features = self.registrar.extract(gray)
        inside = np.array([cv.pointPolygonTest(quad, tuple(map(float, p)), False) >= 0 for p in features.points])
        if int(inside.sum()) < 35:
            self.message = "Zu wenige unterscheidbare Details auf der Seite. Näher heran oder besser beleuchten."
            return False
        features.points = features.points[inside]
        features.descriptors = features.descriptors[inside]
        matrix, self.page_size = page_transform(quad, self.long_side)
        features.matrix, features.frame_id = matrix, 0
        self.keyframes = [features]
        self.anchor_image = cv.warpPerspective(image, matrix, self.page_size, borderValue=(255, 255, 255))
        self.mosaic = cv.warpPerspective(normalize_paper(image), matrix, self.page_size, borderValue=(238, 238, 238))
        overview_gray = cv.cvtColor(self.anchor_image, cv.COLOR_BGR2GRAY)
        self.expected_ink = (overview_gray.astype(np.int16) < cv.GaussianBlur(overview_gray, (0, 0), 7).astype(np.int16) - 12).astype(np.uint8)
        self.seen = np.ones((self.page_size[1], self.page_size[0]), bool)
        self.scores = np.zeros(self.seen.shape, np.float32)
        page_mask = np.zeros(gray.shape, np.uint8)
        cv.fillConvexPoly(page_mask, quad.astype(np.int32), 1)
        page_mask = cv.erode(page_mask, np.ones((11, 11), np.uint8))
        self.text_target = project_text(gray, matrix, self.page_size, page_mask)
        self.boundary_source = source
        self.state = "scanning"
        self.message = "Seite erkannt. Langsam näher herangehen, dann mit mindestens halber Bildüberlappung scannen."
        self.record(image, matrix, {"kind": "overview", "boundary": quad.tolist()}, [])
        self.accepted += 1
        self.update_grid()
        self.revision += 1
        return True

    def feed(self, raw):
        if not self.active or self.state == "mark_page":
            return
        if len(raw) > 2 * 1024 * 1024:
            self.reject("Kamerabild zu groß.", "size")
            return
        try:
            with Image.open(io.BytesIO(raw)) as header:
                if header.width * header.height > 2_500_000 or min(header.size) < 32:
                    raise ValueError("dimensions")
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            self.reject("Ungültiges Scanbild oder zu hohe Auflösung.", "decode")
            return
        image = cv.imdecode(np.frombuffer(raw, np.uint8), cv.IMREAD_COLOR)
        if image is None or image.shape[0] * image.shape[1] > 2_500_000:
            self.reject("Ungültiges Scanbild.", "decode")
            return
        self.frame_count += 1
        self.latest_id += 1
        self.latest = image
        gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
        self.last_quality = assess(gray, overview=self.page_size is None)
        self.detail_diagnostics = {}
        if not self.last_quality["ok"]:
            self.reject(self.last_quality["reason"], "quality")
            return
        if self.page_size is None:
            candidate = detect_page(gray)
            if candidate is None:
                self.candidate_count = 0
                self.candidate = None
                self.message = "Alle vier Seitenkanten zeigen. Kontrastreicher Untergrund hilft; alternativ Rahmen markieren."
                return
            if self.candidate is not None and np.max(np.linalg.norm(candidate - self.candidate, axis=1)) < min(gray.shape) * 0.025:
                self.candidate_count += 1
            else:
                self.candidate_count = 1
            self.candidate = candidate
            self.message = "Seitenrahmen erkannt. Kurz ruhig halten …"
            if self.candidate_count >= 3:
                self.initialize(image, candidate, "detected")
            return
        digest = hashlib.sha256(raw).hexdigest()
        if digest in self.hashes:
            self.message = "Dieser Ausschnitt ist bereits erfasst. Langsam zum nächsten gelben Bereich bewegen."
            return
        features = self.registrar.extract(gray)
        references = [self.keyframes[0]] + list(reversed(self.keyframes[-6:])) if len(self.keyframes) > 1 else self.keyframes
        match = None
        candidates = []
        for reference in references:
            candidate = self.registrar.match(features, reference, gray.shape, self.page_size)
            if candidate:
                depth = 1 + self.sources[reference.frame_id]["metadata"]["registration"].get("chain_depth", 0)
                if depth <= 3:
                    candidate[1]["chain_depth"] = depth
                    candidates.append(candidate)
        candidates.sort(key=lambda candidate: (candidate[1]["reference"] == 0, candidate[1]["inliers"]), reverse=True)
        for matrix, metrics in candidates:
            correlation = agreement(gray, matrix, self.anchor_image, self.seen)
            if correlation >= (0.50 if metrics["reference"] == 0 else 0.75):
                metrics["correlation"] = round(correlation, 3)
                match = matrix, metrics
                break
        if match is None:
            self.lost += 1
            self.reject("Position verloren: etwas zurück zum bereits erfassten Bereich, mehr Überlappung und langsam bewegen.", "tracking")
            return
        self.lost = 0
        matrix, metrics = match
        self.last_polygon = project(corners(gray.shape), matrix)
        expected = cv.warpPerspective(self.expected_ink, np.linalg.inv(matrix), (gray.shape[1], gray.shape[0]), flags=cv.INTER_NEAREST)
        local_scores, blocks = quality_map(gray, matrix, self.page_size, expected, self.detail_diagnostics)
        # Whole-frame extrapolation needs a precise direct anchor fit. Noisy or
        # chained fits may update only the neighborhood of their actual inliers.
        precise_anchor = (metrics["reference"] == 0 and metrics["inliers"] >= 60 and
                          metrics["error_px"] <= .25 and metrics["correlation"] >= .90 and
                          metrics["model"] == "projective")
        support = np.ones(gray.shape, np.uint8) if precise_anchor else supported_pixels(gray.shape, metrics["support_in_source"])
        metrics["support_policy"] = "precise_anchor" if precise_anchor else "inlier_neighborhood"
        local_scores *= support
        warped_scores = cv.warpPerspective(local_scores, matrix, self.page_size)
        visible = cv.warpPerspective(np.full(gray.shape, 255, np.uint8), matrix, self.page_size) > 0
        self.seen |= visible
        better = (warped_scores > self.scores + 0.04) & visible
        gain = float(better.mean())
        # Keep bridge frames while zooming, even before they satisfy reading quality.
        useful_bridge = metrics["inliers"] >= 35 and (len(self.keyframes) == 1 or
            float(np.linalg.norm(matrix[:2, 2] - self.keyframes[-1].matrix[:2, 2])) > 55 or
            abs(np.linalg.det(matrix[:2, :2]) / max(1e-6, np.linalg.det(self.keyframes[-1].matrix[:2, :2])) - 1) > 0.12)
        if gain >= 0.0008 or useful_bridge:
            if len(self.sources) >= self.MAX_SOURCES:
                self.reject("Speichergrenze erreicht. Teilscan speichern und gegebenenfalls neu beginnen.", "limit")
                return
            warped = cv.warpPerspective(normalize_paper(image), matrix, self.page_size, borderValue=(238, 238, 238))
            merge_regions(self.mosaic, warped, better, visible)
            self.scores[better] = warped_scores[better]
            self.add_text(gray, matrix, support)
            features.matrix = matrix
            features.frame_id = len(self.sources)
            page_points = project(features.points, matrix)
            inside = (page_points[:, 0] >= 0) & (page_points[:, 0] < self.page_size[0]) & (page_points[:, 1] >= 0) & (page_points[:, 1] < self.page_size[1])
            features.points = features.points[inside]
            features.descriptors = features.descriptors[inside] if features.descriptors is not None else None
            if len(features.points) >= 35:
                self.keyframes.append(features)
                if len(self.keyframes) > 25:
                    self.keyframes.pop(1)
            self.record(image, matrix, metrics, blocks)
            self.hashes.add(digest)
            self.accepted += 1
            self.revision += 1
        self.update_grid()
        if self.coverage >= 98 and self.accepted >= 4 and self.text_regions_good():
            self.full_confirmations += 1
            self.message = "Abdeckung ausreichend. Kurz ruhig halten zur Abschlussprüfung …"
            if self.full_confirmations >= 3:
                self.state = "complete"
                self.message = "Scan vollständig – erkannte Textbereiche erfasst. Daten bereit (Lesbarkeit geschätzt)."
        else:
            self.full_confirmations = 0
            if not self.text_regions:
                self.message = "Noch keine Textbereiche erkannt. Schrift näher und mit Überlappung zeigen; kein automatischer Abschluss möglich."
            elif self.detail_diagnostics["median_density"] < 0.60:
                self.message = "Position erkannt, aber der Ausschnitt ist noch zu groß. Langsam näher herangehen und Überlappung behalten."
            elif self.detail_diagnostics["usable_frame_percent"] < 5:
                self.message = "Position erkannt. Die Schriftprüfung findet noch zu wenig brauchbare Details; kurz ruhig halten und Licht/Abstand ändern."
            elif gain < 0.0008:
                self.message = "Ausschnitt erkannt und bereits erfasst. Langsam zu einem gelben Bereich bewegen."
            else:
                self.message = "Bereich übernommen. Gelbe Stellen weiter scannen; blau zeigt den aktuellen Ausschnitt."

    def record(self, image, matrix, metrics, blocks):
        self.sources.append({"image": encode(image), "metadata": {
            "index": len(self.sources), "size": [image.shape[1], image.shape[0]],
            "homography_to_page": matrix.tolist(), "registration": metrics,
            "quality": dict(self.last_quality), "text_candidate_boxes_in_source": blocks}})

    def add_text(self, gray, matrix, support):
        detected = project_text(gray, matrix, self.page_size, support)
        if np.any(detected & ~self.text_target):
            self.text_target |= detected
            self.full_confirmations = 0

    def update_grid(self):
        if self.page_size is None:
            return
        self.grid = []
        self.quality_pixel_coverage = round(100 * float((self.scores >= 0.65).mean()), 1)
        self.text_regions = region_quality(self.text_target, self.scores)
        good_pixels = seen_pixels = total_pixels = 0
        w, h = self.page_size
        for y in range(0, h, 80):
            for x in range(0, w, 80):
                x2, y2 = min(w, x + 80), min(h, y + 80)
                score = self.scores[y:y2, x:x2]
                seen = self.seen[y:y2, x:x2]
                target = self.text_target[y:y2, x:x2]
                area = int(target.sum())
                quality_fraction = float((score[target] >= 0.65).mean()) if area else 0
                seen_fraction = float(seen[target].mean()) if area else 0
                state = "ignored" if not area else "good" if quality_fraction >= 0.90 else "weak" if seen_fraction >= 0.5 else "missing"
                total_pixels += area
                good_pixels += int(((score >= .65) & target).sum())
                seen_pixels += int((seen & target).sum())
                self.grid.append({"x": x, "y": y, "width": x2 - x, "height": y2 - y,
                                  "state": state, "target_pixels": area, "quality_fraction": round(quality_fraction, 3)})
        self.coverage = round(100 * good_pixels / total_pixels, 1) if total_pixels else 0
        self.seen_coverage = round(100 * seen_pixels / total_pixels, 1) if total_pixels else 0

    def text_regions_good(self):
        return bool(self.text_regions) and all(r["quality_fraction"] >= .90 for r in self.text_regions)

    def snapshot(self):
        return {"id": self.id, "state": self.state, "message": self.message, "created": self.created,
                "coverage": self.coverage if self.page_size else None, "seen_coverage": self.seen_coverage if self.page_size else None,
                "page_size": self.page_size, "boundary_source": self.boundary_source,
                "coverage_basis": "text", "text_region_count": len(self.text_regions),
                "text_area_percent": round(100 * float(self.text_target.mean()), 1) if self.text_target is not None else 0,
                "accepted": self.accepted, "received": self.frame_count, "rejected": self.rejected,
                "tracking_lost": self.lost > 0, "last_quality": self.last_quality,
                "detail_diagnostics": self.detail_diagnostics, "quality_pixel_coverage": self.quality_pixel_coverage,
                "rejection_counts": dict(self.reason_counts),
                "revision": self.revision, "frozen_id": self.frozen_id, "saved": self.saved,
                "completion_is_estimate": True}

    def preview(self):
        if self.mosaic is None:
            if self.latest is None:
                return None
            image = self.frozen.copy() if self.state == "mark_page" else self.latest.copy()
            if self.candidate is not None and self.state != "mark_page":
                cv.polylines(image, [self.candidate.astype(np.int32)], True, (30, 200, 40), 4)
        else:
            image = self.mosaic.copy()
            tint = image.copy()
            colors = {"good": (85, 185, 75), "weak": (30, 190, 245), "missing": (75, 70, 220)}
            for cell in self.grid:
                if cell["state"] == "ignored":
                    continue
                x, y, w, h = cell["x"], cell["y"], cell["width"], cell["height"]
                region = tint[y:y+h, x:x+w]
                region[self.text_target[y:y+h, x:x+w]] = colors[cell["state"]]
            image = cv.addWeighted(image, 0.70, tint, 0.30, 0)
            if self.last_polygon is not None:
                cv.polylines(image, [self.last_polygon.astype(np.int32)], True, (250, 130, 10), 8)
        scale = min(1, 1000 / max(image.shape[:2]))
        return encode(cv.resize(image, None, fx=scale, fy=scale))

    def finish(self, root):
        if self.mosaic is None:
            raise ValueError("Noch keine Seite erkannt. Zuerst die Übersicht erfassen.")
        complete = self.state == "complete"
        if not complete:
            self.state = "partial"
            self.message = "Teilscan gespeichert – gelbe Bereiche benötigen weitere Aufnahmen."
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        destination = root / self.id
        if destination.exists():
            raise ValueError("Dieser Scan wurde bereits gespeichert.")
        pending = root / ("." + self.id + "-" + uuid.uuid4().hex + ".pending")
        pending.mkdir()
        (pending / "sources").mkdir()
        (pending / "page.png").write_bytes(encode(self.mosaic, ".png"))
        gray = cv.cvtColor(self.mosaic, cv.COLOR_BGR2GRAY)
        document = cv.createCLAHE(clipLimit=1.5, tileGridSize=(12, 12)).apply(gray)
        (pending / "document.png").write_bytes(encode(document, ".png"))
        (pending / "coverage.jpg").write_bytes(self.preview())
        metadata = {**self.snapshot(), "algorithm": "anchored-planar-sift-v3-text", "saved": True,
                    "compositing": "paper-illumination-compensation + 12px inward feather",
                    "reconstruction": getattr(self, "reconstruction", None),
                    "quality_grid": self.grid, "text_regions": self.text_regions, "rejection_counts": self.reason_counts,
                    "sources": [s["metadata"] for s in self.sources], "ocr_text": None,
                    "limitations": ["Coverage and readability are estimates, not OCR verification.",
                                    "Coverage measures detected text candidates inside the initial page boundary; blank margins are excluded.",
                                    "Faint, isolated or unusual text and diagrams may not be detected. Inspect the target map.",
                                    "Requires a static, approximately flat page; no book dewarping.",
                                    "Yellow cells can contain an upscaled overview, not usable close-up detail."]}
        for source in self.sources:
            (pending / "sources" / f'{source["metadata"]["index"]:04d}.jpg').write_bytes(source["image"])
        (pending / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(pending / "scan.zip", "w", compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(pending.rglob("*")):
                if path.is_file() and path.name != "scan.zip":
                    archive.write(path, path.relative_to(pending))
        pending.rename(destination)
        self.saved = True
        self.revision += 1
        return destination
