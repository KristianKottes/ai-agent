"""Add a small 2D sway and optional blinks to a framed portrait photo."""

from itertools import combinations
from math import cos, pi, sin, tau
from pathlib import Path

import cv2
import numpy as np


def _smoothstep(amount):
    amount = np.clip(amount, 0, 1)
    return amount * amount * (3 - 2 * amount)


def _detect_eyes(photo, crop):
    x, y, side = crop
    cascade = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_eye_tree_eyeglasses.xml"))
    if cascade.empty():
        return ()
    upper = cv2.cvtColor(photo[y:y + int(side * 0.53), x:x + side], cv2.COLOR_BGR2GRAY)
    detections = cascade.detectMultiScale(
        cv2.equalizeHist(upper), scaleFactor=1.10, minNeighbors=4,
        minSize=(int(side * 0.07),) * 2, maxSize=(int(side * 0.24),) * 2,
    )
    centers = sorted((left + width / 2, top + height / 2, width) for left, top, width, height in detections)
    pairs = []
    for left, right in combinations(centers, 2):
        dx, dy = right[0] - left[0], abs(right[1] - left[1])
        if (left[0] < side * 0.5 < right[0] and 0.19 * side <= dx <= 0.49 * side
                and dy <= 0.09 * side and min(left[2], right[2]) >= 0.70 * max(left[2], right[2])
                and min(left[1], right[1]) >= 0.19 * side):
            pairs.append((dy + abs(left[2] - right[2]) + abs(left[0] + right[0] - side), left, right))
    if not pairs:
        return ()
    _, left, right = min(pairs)
    return tuple((x + float(cx), y + float(cy), int(width)) for cx, cy, width in (left, right))


class EyeBlink:
    def __init__(self, eye, shape):
        cx, cy, width = eye
        height, columns = shape[:2]
        left, right = max(0, int(cx - 0.61 * width)), min(columns, int(cx + 0.61 * width) + 1)
        top, bottom = max(0, int(cy - 0.44 * width)), min(height, int(cy + 0.44 * width) + 1)
        self.region = np.s_[top:bottom, left:right]
        self.y, self.x = np.mgrid[:bottom - top, :right - left].astype(np.float32)
        self.cy = cy - top
        self.width = width
        self.feather = _smoothstep((0.59 - np.abs(self.x - (cx - left)) / width) / 0.28)

    def close(self, frame, strength):
        # Squeeze the strip containing the opening, borrowing texture from the
        # surrounding lids. Fade the displacement to zero at the patch edges.
        closed = 0.18 * (1 - strength) + 0.018 * strength
        source = np.interp((self.y[:, 0] - self.cy) / self.width,
                           [-0.41, -closed, closed, 0.41], [-0.41, -0.18, 0.18, 0.41])
        displacement = ((source * self.width + self.cy) - self.y[:, 0]).astype(np.float32)
        patch = frame[self.region]
        frame[self.region] = cv2.remap(patch, self.x, self.y + displacement[:, None] * self.feather,
                                      cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


class StillMotion:
    def __init__(self, photo, crop):
        x, y, self.side = crop
        self.eyes = tuple(EyeBlink(eye, photo.shape) for eye in _detect_eyes(photo, crop))
        self.y, self.x = np.mgrid[:photo.shape[0], :photo.shape[1]].astype(np.float32)
        self.dx = self.x - (x + self.side * 0.5)
        self.dy = self.y - (y + self.side * 0.39)
        distance = np.hypot(self.dx / (self.side * 0.82), self.dy / (self.side * 0.87))
        self.feather = _smoothstep((1 - distance) / 0.48)

    def apply(self, frame, seconds):
        # A shallow tilt and drift around the face, with the far background fixed.
        phase = tau * seconds / 4.3
        tilt = sin(phase) * pi / 110
        shift_x = self.side * 0.017 * sin(phase)
        shift_y = self.side * 0.006 * sin(tau * seconds / 3.1)
        c, s = cos(tilt) - 1, sin(tilt)
        map_x = self.x + self.feather * (c * self.dx + s * self.dy - shift_x)
        map_y = self.y + self.feather * (c * self.dy - s * self.dx - shift_y)
        blink_phase = abs((seconds - 1.125 + 1.9) % 3.8 - 1.9)
        if self.eyes and blink_phase < 0.15:
            closure = (1 + cos(pi * blink_phase / 0.15)) / 2
            for eye in self.eyes:
                eye.close(frame, closure)
        return cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
