"""Rebuild into a NEW directory, preserving input.

Usage: python -m app.scanning.rebuild INPUT_FOLDER OUTPUT_ROOT

Recorded geometry is reused. Weak indirect registrations are excluded, and the
coverage is recalculated from the remaining original frames.
"""
import json
from pathlib import Path
import sys
import cv2 as cv
import numpy as np
from app.scanning.engine import ScanEngine
from app.scanning.compositing import normalize_paper, merge_regions, supported_pixels
from app.scanning.geometry import agreement
from app.scanning.quality import quality_map


def rebuild(source, destination):
    source = Path(source)
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    engine = ScanEngine(long_side=max(metadata["page_size"]))
    overview = cv.imread(str(source / "sources" / "0000.jpg"))
    if overview is None:
        raise ValueError("Übersichtsbild fehlt")
    boundary = np.array(metadata["sources"][0]["registration"]["boundary"], np.float32)
    if not engine.initialize(overview, boundary, "reprocessed"):
        raise ValueError("Seitenübersicht nicht verwendbar")
    engine.sources[0]["image"] = (source / "sources" / "0000.jpg").read_bytes()
    excluded = []
    for item in metadata["sources"][1:]:
        index = item["index"]
        raw = (source / "sources" / f"{index:04d}.jpg").read_bytes()
        picture = cv.imdecode(np.frombuffer(raw, np.uint8), cv.IMREAD_COLOR)
        if picture is None:
            raise ValueError(f"Ungültiges Quellbild {index}")
        matrix = np.array(item["homography_to_page"], np.float64)
        gray = cv.cvtColor(picture, cv.COLOR_BGR2GRAY)
        correlation = agreement(gray, matrix, engine.anchor_image, engine.seen)
        direct = engine.registrar.match(engine.registrar.extract(gray), engine.keyframes[0], gray.shape, engine.page_size)
        if direct is None or correlation < .75 or (item["registration"].get("reference", 0) != 0 and correlation < .82):
            excluded.append({"source": index, "correlation": round(correlation, 3)})
            continue
        expected = cv.warpPerspective(engine.expected_ink, np.linalg.inv(matrix), (gray.shape[1], gray.shape[0]), flags=cv.INTER_NEAREST)
        local, blocks = quality_map(gray, matrix, engine.page_size, expected)
        support = supported_pixels(gray.shape, direct[1]["support_in_source"])
        local *= support
        scores = cv.warpPerspective(local, matrix, engine.page_size)
        visible = cv.warpPerspective(np.full(gray.shape, 255, np.uint8), matrix, engine.page_size) > 0
        better = (scores > engine.scores + .04) & visible
        warped = cv.warpPerspective(normalize_paper(picture), matrix, engine.page_size, borderValue=(238, 238, 238))
        merge_regions(engine.mosaic, warped, better, visible)
        engine.scores[better] = scores[better]
        engine.add_text(gray, matrix, support)
        metrics = dict(item["registration"], original_source_index=index, audited_correlation=round(correlation, 3),
                       support_in_source=direct[1]["support_in_source"])
        engine.last_quality = item.get("quality", {})
        engine.record(picture, matrix, metrics, blocks)
        engine.sources[-1]["image"] = raw
        engine.accepted += 1
    engine.frame_count = len(metadata["sources"])
    engine.update_grid()
    engine.reconstruction = {"original_scan": metadata["id"], "excluded_sources": excluded,
                             "geometry_reestimated": False}
    folder = engine.finish(destination)
    print(json.dumps({"folder": str(folder), "coverage": engine.coverage,
                      "retained": engine.accepted, "excluded": len(excluded)}, ensure_ascii=False), flush=True)
    return folder


if __name__ == "__main__":
    rebuild(sys.argv[1], sys.argv[2])
