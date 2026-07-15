import os
import tempfile
import cv2
import numpy as np
from PIL import Image
from typing import Optional, List, Tuple, Dict, Any

class ImageProcessor:
    """
    Unified image processing class handling:
    - Basic image manipulation (sharpen, blur, contrast, etc.)
    - Grid detection
    - Background removal (using segmentation + LLM)
    """
    
    def __init__(self, attention_extractor=None, planner=None, debug_manager=None):
        """
        Args:
            attention_extractor: AttentionExtractor instance (required for background removal)
            planner: ActionPlanner instance (required for background removal)
            debug_manager: DebugManager instance (optional)
        """
        self.attention = attention_extractor
        self.planner = planner
        self.debug = debug_manager
        self._temp_files: List[str] = []

    def __del__(self):
        # Cleanup temp files
        for f in self._temp_files:
            if os.path.exists(f):
                try:
                    os.unlink(f)
                except Exception:
                    pass

    # =========================================================================
    # Static Utility Methods (Stateless)
    # =========================================================================

    @staticmethod
    def detect_movement(image1_path: str, image2_path: str, threshold: float = 0.005) -> bool:
        """
        Compare two images and return True if they are significantly different.
        
        Args:
            image1_path: Path to the first image.
            image2_path: Path to the second image.
            threshold: Percentage of pixels that must change to be considered movement.
        """
        img1 = cv2.imread(image1_path)
        img2 = cv2.imread(image2_path)
        
        if img1 is None or img2 is None:
            return False
            
        if img1.shape != img2.shape:
            # If resolution changed, something definitely changed
            return True
            
        # Compute absolute difference
        diff = cv2.absdiff(img1, img2)
        # Convert to grayscale to simplify
        gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        
        # Threshold the difference to ignore minor noise (compression artifacts, etc.)
        # 30 is a reasonable threshold for significant pixel change
        _, thresh = cv2.threshold(gray_diff, 30, 255, cv2.THRESH_BINARY)
        
        # Calculate percentage of changed pixels
        changed_pixels = cv2.countNonZero(thresh)
        total_pixels = thresh.shape[0] * thresh.shape[1]
        change_ratio = changed_pixels / total_pixels
        
        return change_ratio > threshold

    @staticmethod
    def to_greyscale(image_path: str, output_path: str) -> None:
        """Convert an image to greyscale and save it."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image from {image_path}")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cv2.imwrite(output_path, gray)

    @staticmethod
    def to_edge_outline(image_path: str, output_path: str, low_threshold: int = 15, high_threshold: int = 60) -> None:
        """Detect and isolate edges in an image using Canny edge detection."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image from {image_path}")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (15, 15), 0)
        edges = cv2.Canny(blurred, low_threshold, high_threshold)
        cv2.imwrite(output_path, edges)

    @staticmethod
    def sharpen_image(image_path: str, output_path: str) -> None:
        """Sharpen an image using a generic kernel."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image from {image_path}")

        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        sharpened = cv2.filter2D(img, -1, kernel)
        cv2.imwrite(output_path, sharpened)

    @staticmethod
    def sharpen_edges(image_path: str, output_path: str) -> str:
        """Apply unsharp masking to sharpen edges (alternative to sharpen_image)."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not load image: {image_path}")
            
        gaussian = cv2.GaussianBlur(img, (9, 9), 10.0)
        unsharp = cv2.addWeighted(img, 1.5, gaussian, -0.5, 0, img)
        
        cv2.imwrite(output_path, unsharp)
        return output_path

    @staticmethod
    def apply_clahe(image_path: str, output_path: str, clip_limit: float = 2.0, tile_grid_size: Tuple[int, int] = (8, 8)) -> None:
        """Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image from {image_path}")

        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)

        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
        cl = clahe.apply(l)

        limg = cv2.merge((cl, a, b))
        final = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
        cv2.imwrite(output_path, final)

    @staticmethod
    def apply_contrast_enhancement(image_path: str, output_path: str, alpha: float = 1.5, beta: int = 0) -> None:
        """Apply contrast enhancement using linear stretching."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image from {image_path}")
            
        enhanced = cv2.convertScaleAbs(img, alpha=alpha, beta=beta)
        cv2.imwrite(output_path, enhanced)

    @staticmethod
    def apply_gaussian_blur(image_path: str, output_path: str, kernel_size: Tuple[int, int] = (5, 5), sigma_x: float = 0) -> None:
        """Apply Gaussian Blur to an image."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image from {image_path}")
            
        blurred = cv2.GaussianBlur(img, kernel_size, sigma_x)
        cv2.imwrite(output_path, blurred)

    @staticmethod
    def blend_colors(image_path: str, output_path: str, spatial_radius: int = 10, color_radius: int = 20) -> str:
        """Apply mean shift filtering to blend similar colors (cartoon effect)."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not load image: {image_path}")
            
        filtered = cv2.pyrMeanShiftFiltering(img, sp=spatial_radius, sr=color_radius)
        cv2.imwrite(output_path, filtered)
        return output_path

    @staticmethod
    def merge_similar_colors(image_path: str, output_path: str, k: int = 8) -> None:
        """Quantize image colors to K clusters using K-Means."""
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image from {image_path}")
            
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pixels = img_rgb.reshape((-1, 3))
        pixels = np.float32(pixels)
        
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)
        
        centers = np.uint8(centers)
        quantized = centers[labels.flatten()]
        quantized_img = quantized.reshape(img.shape)
        
        quantized_bgr = cv2.cvtColor(quantized_img, cv2.COLOR_RGB2BGR)
        cv2.imwrite(output_path, quantized_bgr)

    @staticmethod
    def enhance_for_detection(image: Image.Image) -> Image.Image:
        """
        Enhance color distinction (saturation/contrast) for better detection.
        Works on PIL Image.
        """
        # Convert PIL to BGR for OpenCV
        img_np = np.array(image)
        if len(img_np.shape) == 2: # Greyscale
            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_GRAY2BGR)
        elif img_np.shape[2] == 4: # RGBA
             img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGBA2BGR)
        else:
             img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        
        # 1. Color Distinction (Saturation boost)
        # Convert to HSV to easily manipulate saturation
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        
        # Increase saturation significantly to make colors distinct
        # Adding 50 is quite a strong boost
        s = cv2.add(s, 50) 
        
        # Optionally boost Value/Brightness contrast slightly as well
        v = cv2.addWeighted(v, 1.1, np.zeros_like(v), 0, -10)
        
        hsv_enhanced = cv2.merge((h, s, v))
        bgr_enhanced = cv2.cvtColor(hsv_enhanced, cv2.COLOR_HSV2BGR)
        
        # 2. Local Contrast Enhancement (CLAHE)
        # This helps with local structure and making boundaries more distinct
        lab = cv2.cvtColor(bgr_enhanced, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        
        # Use a slightly higher clipLimit for more contrast
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l)
        
        lab_enhanced = cv2.merge((l_enhanced, a, b))
        final_bgr = cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)
        
        # Convert back to PIL RGB
        return Image.fromarray(cv2.cvtColor(final_bgr, cv2.COLOR_BGR2RGB))

    # =========================================================================
    # Background Removal Logic (Stateful)
    # =========================================================================

    # Note: remove_background() (SAM 3-based) was removed in v2. See the
    # `v1-old-architecture` branch if you need to revive segmentation-based
    # background isolation for drag-piece verification.

