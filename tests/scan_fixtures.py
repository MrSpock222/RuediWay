"""Deterministic camera flights over a generated document, no private photos."""
import cv2 as cv
import numpy as np

from app.scanning.geometry import corners


def document(seed=42, decorations=True):
    rng = np.random.default_rng(seed)
    page = np.full((1600, 1200, 3), 242, np.uint8)
    cv.putText(page, "RuediWay DOCUMENT SCAN", (65, 70), cv.FONT_HERSHEY_SIMPLEX, 1.05, (25, 25, 25), 2, cv.LINE_AA)
    for line, y in enumerate(range(125, 1510, 48)):
        number = rng.integers(10000, 99999)
        text = f"{line+1:02d}. Section {number}: x + {line*7+13} = {number % 157} / result."
        cv.putText(page, text, (65, y), cv.FONT_HERSHEY_SIMPLEX, 0.78, (35, 35, 35), 2, cv.LINE_AA)
        cv.circle(page, (1130, y - 9), int(rng.integers(5, 14)), (30, 30, 30), 2)
        line_end = int(rng.integers(400, 1050))
        if decorations:
            cv.line(page, (45, y + 8), (line_end, y + 8), (175, 175, 175), 1)
    for _ in range(35 if decorations else 0):
        x, y = rng.integers(40, 1150), rng.integers(100, 1540)
        cv.circle(page, (int(x), int(y)), 2, (60, 60, 60), -1)
    return page


def overview(page):
    quad = np.float32([[315, 40], [965, 40], [965, 907], [315, 907]])
    transform = cv.getPerspectiveTransform(corners(page.shape), quad)
    frame = cv.warpPerspective(page, transform, (1280, 960), borderValue=(55, 60, 50))
    return frame, quad


def view(page, x, y, width=760, height=570, tilt=0):
    quad = np.float32([[x, y], [x + width, y + tilt], [x + width - tilt, y + height], [x, y + height - tilt]])
    transform = cv.getPerspectiveTransform(quad, corners((960, 1280)))
    return cv.warpPerspective(page, transform, (1280, 960), borderValue=(55, 60, 50))


def jpeg(image):
    return cv.imencode(".jpg", image, [cv.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


def flight():
    positions = [(80, 100, 1080, 810), (0, 0, 800, 600), (400, 0, 800, 600),
                 (400, 350, 800, 600), (0, 350, 800, 600), (0, 700, 800, 600),
                 (400, 700, 800, 600), (400, 1050, 800, 600), (0, 1050, 800, 600)]
    positions += [(x, y, 600, 450) for y in [-40, 250, 550, 850, 1150, 1200] for x in [-40, 320, 660]]
    positions += [(x, y, 1000, 750) for y in [-50, 400, 900] for x in [-50, 250]]
    positions += [(x, y, 900, 675) for y in [-60, 220, 550, 850, 1000] for x in [50, 350]]
    return positions
