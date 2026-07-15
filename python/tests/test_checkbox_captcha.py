import pytest
import json
import os
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from attention import AttentionExtractor

# Paths
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.dirname(TESTS_DIR)
IMAGES_DIR = os.path.join(CORE_DIR, "captchaimages")
CONFIG_PATH = os.path.join(IMAGES_DIR, "targetAreaPercentages.json")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, 'r') as f:
        data = json.load(f)
    config = {}
    for item in data:
        config.update(item)
    return config

TARGET_CONFIG = load_config()

def is_point_in_box(point, box):
    """Check if point (x, y) is inside box [x_min, y_min, x_max, y_max]."""
    x, y = point
    # Strict bounds checking with small tolerance
    tol = 0.001
    return (box[0] - tol <= x <= box[2] + tol and 
            box[1] - tol <= y <= box[3] + tol)

@pytest.fixture(scope="module")
def extractor():
    """Initialize extractor once for all tests."""
    return AttentionExtractor()

# Define checkbox test cases
# Format: (filename, prompt, key)
CHECKBOX_CASES = [
    ("cloudflare.png", "checkbox", "checkbox"),
    ("hcaptchaBasic.png", "checkbox", "checkbox"),
    ("recaptchaBasic.png", "checkbox", "checkbox"),
]

@pytest.mark.parametrize("filename,prompt,key", CHECKBOX_CASES)
def test_checkbox_accuracy(extractor, filename, prompt, key):
    """
    Verify that the AttentionExtractor finds the correct checkbox location.
    """
    image_path = os.path.join(IMAGES_DIR, filename)
    if not os.path.exists(image_path):
        pytest.skip(f"Image not found: {image_path}")
        
    print(f"\nTesting {filename} with prompt '{prompt}'...")
    
    file_config = TARGET_CONFIG.get(filename)
    if not file_config:
        pytest.fail(f"No configuration found for {filename}")
        
    # Get expected box
    regions_list = file_config.get("target_bounding_boxes")
    if not regions_list:
        pytest.fail(f"No target_bounding_boxes for {filename}")
        
    expected_box = None
    for region_map in regions_list:
        if key in region_map:
            expected_box = region_map[key]
            break
            
    if not expected_box:
        pytest.fail(f"Key '{key}' not found in config for {filename}")
        
    # Execute detect
    detections = extractor.detect(image_path, prompt, max_objects=1)
    
    if not detections:
        pytest.fail(f"No objects returned for '{prompt}' in {filename}")
        
    obj = detections[0]
    point = ((obj['x_min'] + obj['x_max']) / 2, (obj['y_min'] + obj['y_max']) / 2)
    print(f"  Result point: {point}")
    print(f"  Expected box: {expected_box}")
    
    assert is_point_in_box(point, expected_box), \
        f"Point {point} not inside expected box {expected_box}"

