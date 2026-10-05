"""Planar page registration with geometric and photometric rejection gates."""
from dataclasses import dataclass

import cv2 as cv
import numpy as np


def project(points, matrix):
    return cv.perspectiveTransform(np.asarray(points, np.float32).reshape(-1, 1, 2), matrix).reshape(-1, 2)


def corners(shape):
    h, w = shape[:2]
    return np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])


def ordered_quad(points):
    points = np.asarray(points, np.float32).reshape(4, 2)
    centre = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1] - centre[1], points[:, 0] - centre[0]))]
    return np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0)


def valid_page_quad(quad, shape):
    h, w = shape[:2]
    quad = ordered_quad(quad)
    if not np.isfinite(quad).all() or not cv.isContourConvex(quad.reshape(-1, 1, 2)):
        return False
    if np.any(quad < 3) or np.any(quad[:, 0] > w - 4) or np.any(quad[:, 1] > h - 4):
        return False
    area = abs(cv.contourArea(quad)) / (w * h)
    edges = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
    return 0.16 < area < 0.97 and edges.min() > min(h, w) * 0.18 and edges.max() / edges.min() < 3.5


def detect_page(gray):
    h, w = gray.shape
    scale = min(1, 800 / max(h, w))
    small = cv.resize(gray, None, fx=scale, fy=scale)
    smooth = cv.GaussianBlur(small, (5, 5), 0)
    edges = cv.Canny(smooth, 40, 120)
    edges = cv.morphologyEx(edges, cv.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    _, bright = cv.threshold(smooth, 0, 255, cv.THRESH_BINARY + cv.THRESH_OTSU)
    candidates = []
    for mask in (edges, bright):
        contours, _ = cv.findContours(mask, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv.contourArea, reverse=True)[:15]:
            quad = cv.approxPolyDP(contour, 0.025 * cv.arcLength(contour, True), True)
            if len(quad) != 4:
                continue
            quad = ordered_quad(quad.reshape(4, 2) / scale)
            if not valid_page_quad(quad, gray.shape):
                continue
            # The starting overview should be near frontal; steep initial angles
            # make the unknown paper aspect ratio unreliable.
            vectors = np.roll(quad, -1, axis=0) - quad
            cosines = [abs(np.dot(vectors[i], vectors[(i + 1) % 4]) /
                           (np.linalg.norm(vectors[i]) * np.linalg.norm(vectors[(i + 1) % 4]))) for i in range(4)]
            if max(cosines) < 0.5:
                candidates.append(quad)
    return max(candidates, key=cv.contourArea) if candidates else None


def page_transform(quad, long_side=2800):
    quad = ordered_quad(quad)
    width = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    height = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    scale = long_side / max(width, height)
    size = (max(600, int(round(width * scale))), max(600, int(round(height * scale))))
    matrix = cv.getPerspectiveTransform(quad, corners((size[1], size[0])))
    return matrix, size


@dataclass
class Features:
    points: np.ndarray
    descriptors: np.ndarray
    matrix: np.ndarray
    frame_id: int


class Registrar:
    def __init__(self):
        self.sift = cv.SIFT_create(nfeatures=2200, contrastThreshold=0.025, edgeThreshold=12)
        self.matcher = cv.BFMatcher(cv.NORM_L2)

    def extract(self, gray):
        scale = min(1, 1100 / max(gray.shape))
        small = cv.resize(gray, None, fx=scale, fy=scale)
        keypoints, descriptors = self.sift.detectAndCompute(small, None)
        points = np.asarray([k.pt for k in keypoints], np.float32).reshape(-1, 2) / scale
        return Features(points, descriptors, np.eye(3), -1)

    def match(self, features, reference, shape, page_size):
        if features.descriptors is None or reference.descriptors is None or len(features.points) < 24:
            return None
        pairs = self.matcher.knnMatch(features.descriptors, reference.descriptors, k=2)
        matches = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.70 * pair[1].distance]
        unique = {}
        for match in sorted(matches, key=lambda m: m.distance):
            unique.setdefault(match.trainIdx, match)
        matches = list(unique.values())
        if len(matches) < 24:
            return None
        src = np.float32([features.points[m.queryIdx] for m in matches])
        dst = np.float32([reference.points[m.trainIdx] for m in matches])
        relative, mask = cv.findHomography(src, dst, cv.RANSAC, 3.0, maxIters=2500, confidence=0.997)
        if relative is None or mask is None:
            return None
        selected = mask.ravel().astype(bool)
        count = int(selected.sum())
        if count < 35 or count / len(matches) < 0.45:
            return None
        hull = cv.convexHull(src[selected])
        if cv.contourArea(hull) < shape[0] * shape[1] * 0.045:
            return None
        error = float(np.median(np.linalg.norm(project(src[selected], relative) - dst[selected], axis=1)))
        # Prefer the lower-variance affine model when perspective does not improve
        # the fit meaningfully. A homography extrapolated from one text column
        # can otherwise bend an untextured margin despite a tiny residual.
        affine, affine_mask = cv.estimateAffine2D(src, dst, method=cv.RANSAC, ransacReprojThreshold=2.0,
                                                 maxIters=2500, confidence=0.997, refineIters=10)
        model = "projective"
        if affine is not None:
            affine_h = np.vstack([affine, [0., 0., 1.]])
            affine_error = float(np.median(np.linalg.norm(project(src[selected], affine_h) - dst[selected], axis=1)))
            if cv.contourArea(hull) < shape[0] * shape[1] * 0.20 and int(affine_mask.sum()) >= count * 0.95 and affine_error <= max(0.35, error * 1.5):
                relative, error, model = affine_h, affine_error, "affine"
        if model == "projective" and cv.contourArea(hull) < shape[0] * shape[1] * 0.12:
            return None
        matrix = reference.matrix @ relative
        matrix /= matrix[2, 2]
        polygon = project(corners(shape), matrix)
        if not np.isfinite(polygon).all() or not cv.isContourConvex(polygon.reshape(-1, 1, 2)):
            return None
        # Reject mirroring, collapsed warps and projective poles inside the frame.
        if cv.contourArea(polygon, oriented=True) <= 0:
            return None
        denominators = corners(shape) @ matrix[2, :2] + matrix[2, 2]
        if np.any(denominators <= 0) or denominators.max() / denominators.min() > 3:
            return None
        area = cv.contourArea(polygon)
        page_area = page_size[0] * page_size[1]
        if not 0.025 * page_area < area < 5 * page_area or error > 2.0:
            return None
        intersection, _ = cv.intersectConvexConvex(polygon, corners((page_size[1], page_size[0])))
        if intersection < min(area, page_area) * 0.12:
            return None
        return matrix, {"inliers": count, "matches": len(matches), "error_px": round(error, 3),
                        "reference": reference.frame_id, "model": model,
                        "support_in_source": hull.reshape(-1, 2).tolist()}


def agreement(gray, matrix, mosaic, seen):
    """Reject plausible letter matches that align different text lines/pages."""
    h, w = mosaic.shape[:2]
    scale = min(1, 850 / max(h, w))
    size = (int(w * scale), int(h * scale))
    down = np.diag([scale, scale, 1.0])
    warped = cv.resize(cv.warpPerspective(gray, matrix, (w, h)), size, interpolation=cv.INTER_AREA)
    mask = cv.warpPerspective(np.full(gray.shape, 255, np.uint8), down @ matrix, size)
    mask = cv.erode(mask, np.ones((9, 9), np.uint8)) > 0
    mask &= cv.resize(seen.astype(np.uint8), size, interpolation=cv.INTER_NEAREST) > 0
    target = cv.resize(cv.cvtColor(mosaic, cv.COLOR_BGR2GRAY), size, interpolation=cv.INTER_AREA)
    a = warped.astype(np.float32) - cv.GaussianBlur(warped, (0, 0), 2).astype(np.float32)
    b = target.astype(np.float32) - cv.GaussianBlur(target, (0, 0), 2).astype(np.float32)
    mask &= (abs(a) > 5) | (abs(b) > 5)
    if mask.sum() < 100:
        return 0.0
    av, bv = a[mask], b[mask]
    return float(np.dot(av, bv) / max(1, np.linalg.norm(av) * np.linalg.norm(bv)))
