"""Text-shaped regions, without OCR. Detection is deliberately only an estimate."""
import cv2 as cv
import numpy as np


def detect_text(gray):
    """Group native-pixel character candidates into small word/line regions.

    Do not detect on the enlarged mosaic: interpolation must not invent detail.
    Long page edges, rules and isolated speckles cannot form text groups.
    """
    background = cv.GaussianBlur(gray, (0, 0), 5)
    ink = (gray.astype(np.int16) < background.astype(np.int16) - 12).astype(np.uint8)
    count, labels, stats, _ = cv.connectedComponentsWithStats(ink, 8)
    characters = np.zeros(count, bool)
    for i in range(1, count):
        _, _, w, h, area = stats[i]
        characters[i] = (1 <= w <= 120 and 3 <= h <= 80 and 4 <= area <= 3000
                         and w / h < 8 and h / w < 12)
    target = np.zeros_like(gray, np.uint8)
    if not characters.any():
        return target
    height = float(np.median(stats[characters, cv.CC_STAT_HEIGHT]))
    kernel = (max(5, int(height * 1.3)), max(3, int(height * .25)))
    groups = cv.dilate(characters[labels].astype(np.uint8), np.ones(kernel[::-1], np.uint8))
    contours, _ = cv.findContours(groups, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv.boundingRect(contour)
        ids = np.unique(labels[y:y+h, x:x+w])
        if int(characters[ids].sum()) >= 3:
            target[y:y+h, x:x+w] = 1
    # Include adjacent numbering and punctuation even when separated from words.
    # Isolated marks elsewhere on an otherwise blank sheet remain unclassified.
    nearby = cv.dilate(target, np.ones((max(3, int(height)), max(7, int(height * 6))), np.uint8))
    padding = max(1, int(height * .2))
    for i in np.flatnonzero(characters):
        x, y, w, h, _ = stats[i]
        if nearby[y:y+h, x:x+w].any():
            target[max(0, y-padding):y+h+padding, max(0, x-padding):x+w+padding] = 1
    return target


def project_text(gray, matrix, page_size, support):
    # Exclude desk details before grouping, so they cannot seed page targets.
    masked = gray.copy()
    masked[support == 0] = 255
    detected = detect_text(masked) & support.astype(np.uint8)
    return cv.warpPerspective(detected, matrix, page_size, flags=cv.INTER_NEAREST).astype(bool)


def region_quality(target, scores):
    """Each separate text region must be readable, including short final lines."""
    count, labels, stats, _ = cv.connectedComponentsWithStats(target.astype(np.uint8), 8)
    good = np.bincount(labels[scores >= .65], minlength=count)
    return [{"x": int(stats[i, 0]), "y": int(stats[i, 1]),
             "width": int(stats[i, 2]), "height": int(stats[i, 3]),
             "pixels": int(stats[i, 4]),
             "quality_fraction": round(float(good[i] / stats[i, 4]), 4)}
            for i in range(1, count)]
