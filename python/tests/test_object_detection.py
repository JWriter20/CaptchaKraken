import pytest
import json
import os
import sys
import numpy as np
from PIL import Image

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

def check_containment(inner, outer, threshold=0.9):
    """Check if inner box is contained in outer box."""
    if (inner[0] >= outer[0] and inner[1] >= outer[1] and 
        inner[2] <= outer[2] and inner[3] <= outer[3]):
        return True

    ix_min = max(inner[0], outer[0])
    iy_min = max(inner[1], outer[1])
    ix_max = min(inner[2], outer[2])
    iy_max = min(inner[3], outer[3])
    
    if ix_max < ix_min or iy_max < iy_min:
        return False
        
    intersection_area = (ix_max - ix_min) * (iy_max - iy_min)
    inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    
    if inner_area == 0:
        return False
        
    ratio = intersection_area / inner_area
    return ratio >= threshold

@pytest.fixture(scope="module")
def extractor():
    return AttentionExtractor()

GRID_CASES = [
    ("hcaptchaImages1.png", "bird", ["topMiddleBirdGroup", "topRightBirdGroup", "bottomLeftBirdGroup"]),
    ("recaptchaImages.png", "car", ["promptCar", "topMiddleCarGroup", "middleRightCarGroup", "bottomRightCar"]),
    ("recaptchaImages2.png", "motorcycle", ["motorcycleContainer"]),
    ("recaptchaImages3.png", "fire hydrant", ["bottomLeftFireHydrant", "middleFireHydrant", "topRightFireHydrant"]),
    ("hcaptchaDragImage1.png", "top segment movable square", ["topSegment"]),
    ("hcaptchaDragImage2.png", "top movable deer head", ["deerhead"]),
    ("hcaptchaDragImages3.png", "bottom right movable bee", ["bee"]),
    ("hcaptchaDragImages3.png", "top left strawberry", ["beeDesinationStrawberry"]),
]

@pytest.mark.parametrize("filename,prompt,valid_keys", GRID_CASES)
def test_image_detection(extractor, filename, prompt, valid_keys):
    image_path = os.path.join(IMAGES_DIR, filename)
    if not os.path.exists(image_path):
        pytest.skip(f"Image not found: {image_path}")
        
    file_config = TARGET_CONFIG.get(filename)
    if not file_config:
        pytest.fail(f"No configuration found for {filename}")
        
    regions_list = file_config.get("target_bounding_boxes") or file_config.get("target_area_percentages")
    valid_boxes = []
    if regions_list:
        for region_map in regions_list:
            for key, box in region_map.items():
                if key in valid_keys:
                    valid_boxes.append((key, box))
    
    if not valid_boxes:
        pytest.fail(f"Could not find valid boxes for keys {valid_keys} in config")
        
    detections = extractor.detect(image_path, prompt)
    assert len(detections) > 0, f"No detections found for '{prompt}' in {filename}"
        
    for det in detections:
        bbox = [det['x_min'], det['y_min'], det['x_max'], det['y_max']]
        is_valid = False
        for key, valid_box in valid_boxes:
            if check_containment(bbox, valid_box, threshold=0.85):
                is_valid = True
                break
        assert is_valid, f"Detection {bbox} is outside expected regions: {valid_keys} in {filename}"

def test_video_detection(extractor):
    """
    Test object detection and numbering in a video file.
    """
    import cv2
    video_path = os.path.join(IMAGES_DIR, "hcaptcha_1766539373078.webm")
    if not os.path.exists(video_path):
        pytest.skip(f"Video not found: {video_path}")
        
    cap = cv2.VideoCapture(video_path)
    success, frame = cap.read()
    cap.release()
    
    if not success:
        pytest.fail("Failed to read frame from video")
        
    # Save frame as temporary image for AttentionExtractor
    temp_frame_path = "temp_video_frame.png"
    cv2.imwrite(temp_frame_path, frame)
    
    try:
        # Prompt for objects in this specific video (hcaptcha puzzle/objects)
        prompt = "object" 
        detections = extractor.detect(temp_frame_path, prompt)
        
        assert len(detections) > 0, "No objects detected in video frame"
        
        # Verify we can number them (just check we have enough detections to number)
        # and that they have valid bounding boxes
        for i, det in enumerate(detections):
            assert 'x_min' in det and 'y_min' in det
            assert 'x_max' in det and 'y_max' in det
            assert det['x_min'] < det['x_max']
            assert det['y_min'] < det['y_max']
            
        print(f"Detected and numbered {len(detections)} objects in video frame")
        
    finally:
        if os.path.exists(temp_frame_path):
            os.remove(temp_frame_path)

