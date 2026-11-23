"""Track the first frame's face crop through a continuous reference clip."""

import cv2
import numpy as np

SIZE = 640
MODEL_SIZE = 256


class FaceTrackingError(ValueError):
    pass


class FaceTracker:
    def __init__(self, frame, rectangle):
        self.x, self.y, self.side = rectangle
        self.previous_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        region = np.zeros_like(self.previous_gray)
        margin = round(self.side * 0.15)
        # Use the upper face so changes in the reference mouth do not move the crop.
        region[self.y + margin:self.y + round(self.side * 0.57),
               self.x + margin:self.x + self.side - margin] = 255
        points = cv2.goodFeaturesToTrack(self.previous_gray, 96, 0.01, 5, mask=region)
        if points is None or len(points) < 8:
            raise FaceTrackingError("The first frame has too few trackable features in the face box")
        self.initial = points.copy()
        self.previous_points = points

    def locate(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        current, forward, error = cv2.calcOpticalFlowPyrLK(self.previous_gray, gray, self.previous_points, None)
        if current is None:
            raise FaceTrackingError("The face features were lost")
        back, backward, _ = cv2.calcOpticalFlowPyrLK(gray, self.previous_gray, current, None)
        if back is None:
            raise FaceTrackingError("The face features could not be checked")
        distance = np.linalg.norm((back - self.previous_points).reshape(-1, 2), axis=1)
        good = (forward.ravel() == 1) & (backward.ravel() == 1) & (distance < 1.25) & (error.ravel() < 22)
        if np.count_nonzero(good) < 8:
            raise FaceTrackingError("The face moved too far or the reference contains a cut")
        initial, points = self.initial[good], current[good]
        matrix, inliers = cv2.estimateAffinePartial2D(initial, points, ransacReprojThreshold=2.5)
        if matrix is None or np.count_nonzero(inliers) < max(8, len(points) * 0.65):
            raise FaceTrackingError("The remaining features disagree on how the face moved")

        x, y, side = self.x, self.y, self.side
        corners = np.float32([[[x, y], [x + side, y], [x, y + side], [x + side, y + side]]])
        mapped = cv2.transform(corners, matrix)
        scale = np.linalg.norm(matrix[:, 0])
        if not 0.72 <= scale <= 1.42 or mapped.min() < -1 or mapped.max() > SIZE + 1:
            raise FaceTrackingError("The face box left the video or changed size too much")

        accepted = inliers.ravel() == 1
        self.initial = initial[accepted]
        self.previous_points = points[accepted]
        self.previous_gray = gray
        origin = matrix[:, :2] @ np.float64([x, y]) + matrix[:, 2]
        return np.column_stack((matrix[:, :2] * side / MODEL_SIZE, origin))


def tracked_frames(path, rectangle, count):
    source = cv2.VideoCapture(str(path))
    try:
        x, y, side = rectangle
        transform = np.float64([[side / MODEL_SIZE, 0, x], [0, side / MODEL_SIZE, y]])
        for index in range(count):
            ok, frame = source.read()
            if not ok or frame.shape[:2] != (SIZE, SIZE):
                raise FaceTrackingError("The prepared reference ended before the requested clip")
            if index == 0:
                tracker = FaceTracker(frame, rectangle)
            else:
                transform = tracker.locate(frame)
            reference = cv2.warpAffine(
                frame, transform, (MODEL_SIZE, MODEL_SIZE), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            )
            yield frame, reference, transform
    finally:
        source.release()
