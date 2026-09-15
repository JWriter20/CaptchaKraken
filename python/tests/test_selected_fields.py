import os
import sys
import pytest
import json
import glob
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from captchakraken.tool_calls.find_grid import find_grid, detect_selected_cells

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
RECAPTCHA_DIR = os.path.join(PROJECT_ROOT, "captchaimages", "coreRecaptcha")
ANSWERS_PATH = os.path.join(RECAPTCHA_DIR, "recaptchaAnswers.json")

def test_selected_fields_all_images():
    if not os.path.exists(RECAPTCHA_DIR):
        pytest.skip(f"Directory not found: {RECAPTCHA_DIR}")

    if not os.path.exists(ANSWERS_PATH):
        pytest.skip(f"Answers file not found: {ANSWERS_PATH}")

    with open(ANSWERS_PATH, 'r') as f:
        answers_data = json.load(f)

    failures = []
    debug_base_dir = Path("latestDebugRun").resolve()

    print(f"\nTesting images in {RECAPTCHA_DIR} against {ANSWERS_PATH}")

    for filename, data in answers_data.items():
        image_path = os.path.join(RECAPTCHA_DIR, filename)

        if not os.path.exists(image_path):
            print(f"Warning: Image {filename} not found at {image_path}. Skipping.")
            continue

        print(f"Testing {filename}...")

        image_basename = os.path.splitext(filename)[0]

        grid_boxes = find_grid(image_path)

        if not grid_boxes:
            expected_selected = data.get("selectedCells", [])
            if expected_selected:
                 failures.append(f"{filename}: No grid detected, but expected selection {expected_selected}")
            else:
                 print(f"  No grid detected in {filename}. Expecting empty selection. OK.")
            continue

        selected_indices, loading_indices = detect_selected_cells(image_path, grid_boxes)
        selected_indices.sort()

        expected_selected = data.get("selectedCells", [])
        expected_selected.sort()
        

        if selected_indices != expected_selected:
            error_msg = f"{filename}: Expected {expected_selected}, got {selected_indices}"
            print(f"  FAILED: {error_msg}")
            failures.append(error_msg)
        else:
            print(f"  SUCCESS: Matches {expected_selected}")
            if debug_base_dir.exists():
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
    if not os.path.exists(RECAPTCHA_DIR):
        print(f"Error: Directory not found: {RECAPTCHA_DIR}")
        sys.exit(1)

    if not os.path.exists(ANSWERS_PATH):
        print(f"Error: Answers file not found: {ANSWERS_PATH}")
        sys.exit(1)

    test_selected_fields_all_images()
