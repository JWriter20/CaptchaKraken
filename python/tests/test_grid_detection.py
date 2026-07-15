import pytest
import os
import sys
import glob

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import find_grid
from captchakraken.solver import DebugManager

# Paths
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.dirname(TESTS_DIR)
IMAGES_DIR = os.path.join(CORE_DIR, "captchaimages")
RECAPTCHA_DIR = os.path.join(IMAGES_DIR, "coreRecaptcha")

def get_test_images():
    """Collect images for grid detection testing."""
    images = []
    
    # Specific images requested
    images.append(os.path.join(IMAGES_DIR, "hcaptchaImages1.png"))
    images.append(os.path.join(IMAGES_DIR, "slantedGrid.png"))
    
    # All PNG images in coreRecaptcha
    recaptcha_images = glob.glob(os.path.join(RECAPTCHA_DIR, "*.png"))
    # Filter out some that are known to not have clear lines if necessary, 
    # but the user asked for all of coreRecaptcha.
    images.extend(recaptcha_images)
    
    # Filter to only existing files
    return [img for img in images if os.path.exists(img)]

@pytest.fixture(scope="module")
def debug_manager():
    """Initialize a single DebugManager for all tests in this module."""
    return DebugManager(debug_enabled=True)

@pytest.mark.parametrize("image_path", get_test_images())
@pytest.mark.parametrize("slant", [0.0, 0.015, -0.015])
def test_grid_detection_on_image(image_path, debug_manager, slant):
    """
    Unified test for grid detection across multiple captcha images.
    Verifies that find_grid returns a valid 3x3 or 4x4 grid.
    """
    filename = os.path.basename(image_path)
    
    # Only test non-zero slants for the slanted grid image to save time
    if slant != 0.0 and "slantedGrid" not in filename:
        pytest.skip(f"Skipping slant {slant} for non-slanted image {filename}")

    # Call find_grid with the module-scoped debug manager and explicit slant
    grid_boxes = find_grid(image_path, debug_manager=debug_manager, slant_to_try=slant)
    
    # Every image in this test set is EXPECTED to have a grid.
    assert grid_boxes is not None, f"find_grid failed to detect any grid in {filename} with slant {slant}"
    
    # Check that we have exactly 9 (3x3) or 16 (4x4) boxes
    num_boxes = len(grid_boxes)
    assert num_boxes in [9, 16], f"Detected {num_boxes} boxes in {filename}, expected 9 or 16"
    
    # Check for similar dimensions
    widths = [box[2] - box[0] for box in grid_boxes]
    heights = [box[3] - box[1] for box in grid_boxes]
    
    avg_width = sum(widths) / len(widths)
    avg_height = sum(heights) / len(heights)
    
    # Allow 10% tolerance from average
    for i, (w, h) in enumerate(zip(widths, heights)):
        assert 0.9 * avg_width <= w <= 1.1 * avg_width, f"Box {i} in {filename} width {w} deviates too much from avg {avg_width}"
        assert 0.9 * avg_height <= h <= 1.1 * avg_height, f"Box {i} in {filename} height {h} deviates too much from avg {avg_height}"
    
    # Validate each box structure
    for i, box in enumerate(grid_boxes):
        assert len(box) == 4, f"Box {i} in {filename} should have 4 coordinates, got {len(box)}"
        x1, y1, x2, y2 = box
        assert x1 < x2, f"Box {i} in {filename}: x1 ({x1}) should be < x2 ({x2})"
        assert y1 < y2, f"Box {i} in {filename}: y1 ({y1}) should be < y2 ({y2})"
        assert x1 >= 0 and y1 >= 0, f"Box {i} in {filename}: coordinates should be non-negative"

if __name__ == "__main__":
    # Allow running this file directly
    test_images = get_test_images()
    for img in test_images:
        try:
            test_grid_detection_on_image(img)
            print(f"✓ {os.path.basename(img)} passed")
        except Exception as e:
            print(f"✗ {os.path.basename(img)} failed: {e}")
