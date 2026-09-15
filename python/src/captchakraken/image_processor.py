"""The frame-diff primitive every movement check in both ports shares."""

import cv2


class ImageProcessor:
    @staticmethod
    def movement_ratio(image1_path: str, image2_path: str) -> float:
        """Share of pixels differing by more than 30 grey levels; 0 if unreadable, 1 if the sizes differ.

        Split from `detect_movement` so callers can report the number: GeeTest's permanent background shimmer
        sits close enough to the default threshold to fire the freshness re-solve on nearly every inference.
        """
        img1, img2 = cv2.imread(image1_path), cv2.imread(image2_path)
        if img1 is None or img2 is None:
            return 0.0
        if img1.shape != img2.shape:
            return 1.0
        gray = cv2.cvtColor(cv2.absdiff(img1, img2), cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
        return cv2.countNonZero(thresh) / float(thresh.shape[0] * thresh.shape[1])

    @staticmethod
    def detect_movement(image1_path: str, image2_path: str, threshold: float = 0.005) -> bool:
        return ImageProcessor.movement_ratio(image1_path, image2_path) > threshold
