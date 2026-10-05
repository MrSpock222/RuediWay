"""Native-pixel quality estimates. They are not a guarantee of OCR readability."""
import cv2 as cv
import numpy as np

from .geometry import project


def assess(gray, overview=False):
    smooth = cv.GaussianBlur(gray, (3, 3), 0.6)
    lap = cv.Laplacian(smooth, cv.CV_32F)
    sharpness = float(lap.var())
    contrast = float(np.percentile(gray, 99) - np.percentile(gray, 1))
    if float((gray < 10).mean()) > 0.95 or float((gray > 250).mean()) > 0.995:
        return {"ok": False, "reason": "Belichtung prüfen: Bild fast vollständig schwarz oder weiß.", "sharpness": sharpness}
    # Only reject unusable input globally. White margins and low ink contrast
    # strongly reduce whole-image Laplacian variance even for readable letters.
    # Native local strokes, registration and density decide readable coverage.
    if sharpness < 0.6 or contrast < 6:
        return {"ok": False, "reason": "Kamera kurz ruhig halten; Abstand und Schärfe prüfen.", "sharpness": sharpness}
    return {"ok": True, "reason": "", "sharpness": sharpness, "contrast": contrast}


def quality_map(gray, matrix, page_size=None, expected_ink=None, diagnostics=None):
    h, w = gray.shape
    page_mask = np.ones((h, w), np.uint8)
    if page_size is not None:
        pw, ph = page_size
        outline = project([[0, 0], [pw - 1, 0], [pw - 1, ph - 1], [0, ph - 1]], np.linalg.inv(matrix))
        page_mask[:] = 0
        cv.fillConvexPoly(page_mask, np.clip(outline, -100000, 100000).astype(np.int32), 1)
    interior = cv.erode(page_mask, np.ones((11, 11), np.uint8)).astype(bool)
    # Local dark strokes are only text candidates, never transcribed/interpreted.
    background = cv.GaussianBlur(gray, (0, 0), 5)
    ink = (gray.astype(np.int16) < background.astype(np.int16) - 12).astype(np.uint8)
    ink[~interior] = 0
    count, labels, stats, _ = cv.connectedComponentsWithStats(ink, 8)
    heights = np.zeros(count, np.float32)
    graphics = np.zeros(count, bool)
    for i in range(1, count):
        x, y, cw, ch, area = stats[i]
        if 2 <= cw <= 100 and 4 <= ch <= 65 and 5 <= area <= 2500:
            heights[i] = ch
        graphics[i] = area >= 20 and ((cw > 100 and ch <= 8) or (ch > 100 and cw <= 8))
    charmap = heights[labels]
    graphic_mask = graphics[labels]
    scores = np.zeros_like(gray, np.float32)
    text_mask = (charmap > 0).astype(np.uint8)
    detail_neighborhood = cv.dilate((text_mask | graphic_mask).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    lap = cv.Laplacian(cv.GaussianBlur(gray, (3, 3), 0.6), cv.CV_32F)
    densities = []
    for y in range(0, h, 80):
        for x in range(0, w, 80):
            y2, x2 = min(h, y + 80), min(w, x + 80)
            valid = interior[y:y2, x:x2]
            tile = gray[y:y2, x:x2][valid]
            if tile.size < 20:
                continue
            chars = charmap[y:y2, x:x2]
            values = chars[chars > 0]
            component_ids = np.unique(labels[y:y2, x:x2])
            component_count = int((heights[component_ids] > 0).sum())
            centre = [(x + x2) / 2, (y + y2) / 2]
            projected = project([centre, [centre[0] + 1, centre[1]], [centre[0], centre[1] + 1]], matrix)
            jacobian = np.column_stack([projected[1] - projected[0], projected[2] - projected[0]])
            density = 1 / max(0.01, np.sqrt(abs(np.linalg.det(jacobian))))
            densities.append(float(density))
            # Do not call an upscaled, distant overview a readable close-up.
            if density < 0.60:
                continue
            known_detail = expected_ink is not None and float(expected_ink[y:y2, x:x2][valid].mean()) > 0.015
            if not known_detail and component_count < 3 and len(values) < max(3, tile.size * 0.02) and np.percentile(tile, 5) > 85:
                quality = 0.90  # observed blank paper, inferred using the registered plane
            else:
                detail = detail_neighborhood[y:y2, x:x2] & valid
                sharpness = float(lap[y:y2, x:x2][detail if detail.sum() >= 20 else valid].var())
                graphic = graphic_mask[y:y2, x:x2]
                foreground = gray[y:y2, x:x2][((chars > 0) | graphic) & valid]
                contrast = float(np.percentile(tile, 95) - np.median(foreground)) if len(foreground) else float(np.percentile(tile, 98) - np.percentile(tile, 2))
                height = float(np.median(values)) if len(values) else 9 if graphic.any() else 4
                # Laplacian energy changes quadratically with ink contrast.
                # Normalize within the stroke region; keep an absolute contrast
                # floor so near-blank sensor noise does not become "sharp text".
                normalized_sharpness = sharpness * (100 / max(20, contrast)) ** 2
                quality = min(1, normalized_sharpness / 55, max(0, contrast) / 30, height / 9)
            scores[y:y2, x:x2] = min(quality, density / 0.78)
    scores[page_mask == 0] = 0
    scores[:10] = scores[-10:] = 0
    scores[:, :10] = scores[:, -10:] = 0
    if diagnostics is not None:
        diagnostics.update({"median_density": round(float(np.median(densities)), 3) if densities else 0,
                            "usable_frame_percent": round(100 * float((scores[page_mask > 0] >= 0.65).mean()), 1) if page_mask.any() else 0})
    blocks_mask = cv.dilate(text_mask, cv.getStructuringElement(cv.MORPH_RECT, (21, 7)))
    contours, _ = cv.findContours(blocks_mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    blocks = [list(cv.boundingRect(c)) for c in contours if cv.contourArea(c) > 60][:200]
    return scores, blocks
