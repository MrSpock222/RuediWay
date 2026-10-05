"""Display-only illumination compensation; source pixels/quality stay unchanged."""
import cv2 as cv
import numpy as np


def supported_pixels(shape, hull):
    """Only modest extrapolation beyond matched text, not entire blank margins."""
    mask = np.zeros(shape[:2], np.uint8)
    points = np.asarray(hull, np.float32).reshape(-1, 2)
    if len(points) >= 3 and np.isfinite(points).all():
        cv.fillConvexPoly(mask, cv.convexHull(points).astype(np.int32), 1)
        margin = max(3, int(min(shape[:2]) * .04))
        mask = cv.dilate(mask, cv.getStructuringElement(cv.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1)))
    return mask


def normalize_paper(image):
    h, w = image.shape[:2]
    scale = min(1., 640 / max(h, w))
    small = cv.resize(image, None, fx=scale, fy=scale, interpolation=cv.INTER_AREA)
    background = cv.morphologyEx(small, cv.MORPH_CLOSE,
                                cv.getStructuringElement(cv.MORPH_ELLIPSE, (31, 31)))
    background = cv.GaussianBlur(background, (0, 0), 12)
    background = cv.resize(background, (w, h)).astype(np.float32)
    # Limit correction on dark/solid graphics so they are not washed white.
    floor = np.maximum(60., np.percentile(small, 90, axis=(0, 1)) * .65)
    gain = np.clip(238. / np.maximum(background, floor), .7, 2.5)
    return np.clip(image.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def merge_regions(mosaic, warped, better, visible):
    """Feather a narrow incoming edge; copy the region interior exactly.

    Do not average entire text lines across many source frames. The alpha stays
    inside the better-quality region and never creates extra good coverage.
    """
    if not np.any(better):
        return
    distance = cv.distanceTransform(better.astype(np.uint8), cv.DIST_L2, 3)
    alpha = np.minimum(1., distance / 12.) * visible
    changed = alpha > 0
    weight = alpha[changed, None]
    mosaic[changed] = np.clip(mosaic[changed] * (1 - weight) + warped[changed] * weight, 0, 255).astype(np.uint8)
