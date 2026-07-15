import os
import sys
import pytest
import json
import glob
from pathlib import Path

# Add project root to path before imports
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from captchakraken.solver import DebugManager
from captchakraken.tool_calls.find_grid import find_grid, detect_selected_cells

# Paths
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
RECAPTCHA_DIR = os.path.join(PROJECT_ROOT, "captchaimages", "coreRecaptcha")
ANSWERS_PATH = os.path.join(RECAPTCHA_DIR, "recaptchaAnswers.json")

def test_selected_fields_all_images():
    """
    Test that detected selections match expectations from recaptchaAnswers.json.
    """
    # These checks are now handled in __main__ block, but kept here for pytest compatibility
    if not os.path.exists(RECAPTCHA_DIR):
        pytest.skip(f"Directory not found: {RECAPTCHA_DIR}")
        
    if not os.path.exists(ANSWERS_PATH):
        pytest.skip(f"Answers file not found: {ANSWERS_PATH}")

    with open(ANSWERS_PATH, 'r') as f:
        answers_data = json.load(f)

    # Initialize DebugManager
    debug_manager = DebugManager(debug_enabled=True)
    
    failures = []
    debug_base_dir = Path("latestDebugRun").resolve()
    
    print(f"\nTesting images in {RECAPTCHA_DIR} against {ANSWERS_PATH}")

    for filename, data in answers_data.items():
        image_path = os.path.join(RECAPTCHA_DIR, filename)
        
        if not os.path.exists(image_path):
            print(f"Warning: Image {filename} not found at {image_path}. Skipping.")
            continue
            
        print(f"Testing {filename}...")
        
        # Extract base filename for debug image matching
        image_basename = os.path.splitext(filename)[0]
        
        # 1. Detect Grid
        grid_boxes = find_grid(image_path)
        
        if not grid_boxes:
            # If we expect selections, but can't find a grid, that's a failure.
            # If expected selections are empty, maybe it's fine? 
            # But usually these are grid captchas, so no grid is likely an error in detection or non-grid image.
            # However, looking at the dataset, most should have grids.
            expected_selected = data.get("selectedCells", [])
            if expected_selected:
                 failures.append(f"{filename}: No grid detected, but expected selection {expected_selected}")
            else:
                 print(f"  No grid detected in {filename}. Expecting empty selection. OK.")
            continue
            
        # 2. Detect Selections
        selected_indices, loading_indices = detect_selected_cells(image_path, grid_boxes, debug_manager)
        selected_indices.sort()
        
        expected_selected = data.get("selectedCells", [])
        expected_selected.sort()
        
        # We only verify selectedCells (completed selections) as per request.
        # loading_indices are ignored for this test unless we want to verify them too, 
        # but the JSON only has selectedCells.
        
        if selected_indices != expected_selected:
            error_msg = f"{filename}: Expected {expected_selected}, got {selected_indices}"
            print(f"  FAILED: {error_msg}")
            failures.append(error_msg)
            # Keep debug images for failures
        else:
            print(f"  SUCCESS: Matches {expected_selected}")
            # Clean up debug images for passing tests
            if debug_base_dir.exists():
                # Find all debug images for this test case
                pattern = f"badge_analysis_{image_basename}_*"
                debug_images = list(debug_base_dir.glob(pattern))
                for debug_img in debug_images:
                    try:
                        debug_img.unlink()
                        print(f"  Cleaned up debug image: {debug_img.name}")
                    except Exception as e:
                        print(f"  Warning: Could not delete {debug_img.name}: {e}")

    if failures:
        print(f"\n{'='*60}")
        print("TEST FAILURES:")
        print('='*60)
        for failure in failures:
            print(failure)
        print('='*60)
        sys.exit(1)
    else:
        print(f"\n{'='*60}")
        print("ALL TESTS PASSED!")
        print('='*60)

if __name__ == "__main__":
    # When running directly, handle skips differently
    if not os.path.exists(RECAPTCHA_DIR):
        print(f"Error: Directory not found: {RECAPTCHA_DIR}")
        sys.exit(1)
        
    if not os.path.exists(ANSWERS_PATH):
        print(f"Error: Answers file not found: {ANSWERS_PATH}")
        sys.exit(1)
    
    test_selected_fields_all_images()
