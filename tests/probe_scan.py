"""Reproducible acquisition benchmark; run from the repository root."""
import json
from pathlib import Path
import time
import sys

import cv2 as cv
import numpy as np
from app.scanning.geometry import corners, project

from app.scanning.engine import ScanEngine
from scan_fixtures import document, overview, view, jpeg, flight

page = document(decorations="--plain" not in sys.argv)
first, quad = overview(page)
engine = ScanEngine(long_side=2800)
engine.initialize(first, quad, "manual")
page_matrix = engine.keyframes[0].matrix @ cv.getPerspectiveTransform(corners(page.shape), quad)
start = time.monotonic()
for index, parameters in enumerate(flight()):
    before = engine.accepted
    engine.feed(jpeg(view(page, *parameters, tilt=5)))
    if engine.accepted > before:
        x, y, width, height = parameters
        source_quad = np.float32([[x,y],[x+width,y+5],[x+width-5,y+height],[x,y+height-5]])
        actual = project(corners((960,1280)), np.array(engine.sources[-1]['metadata']['homography_to_page']))
        wanted = project(source_quad, page_matrix)
        print('  corner error', np.round(np.linalg.norm(actual-wanted,axis=1),1),engine.sources[-1]['metadata']['registration'])
    print(index, engine.coverage, engine.accepted, engine.message, flush=True)
for brightness in [1, 2, 3]:
    engine.feed(jpeg(cv.convertScaleAbs(view(page, 250, 900, 1000, 750, tilt=5), beta=brightness)))
print("Final", engine.coverage, engine.state)
folder = Path("scan-validation")
folder.mkdir(exist_ok=True)
(folder / "coverage.jpg").write_bytes(engine.preview())
cv.imwrite(str(folder / "mosaic.png"), engine.mosaic)
cv.imwrite(str(folder / "source.png"), page)
(folder / "metrics.json").write_text(json.dumps(engine.snapshot(), indent=2), encoding="utf-8")
print("Seconds", round(time.monotonic() - start, 2), flush=True)
