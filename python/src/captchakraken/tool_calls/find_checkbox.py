import cv2
import numpy as np
from typing import Optional, Tuple

def find_checkbox(image_path: str) -> Optional[Tuple[int, int, int, int]]:
    """
    Detects a checkbox in the image using lightweight computer vision techniques.
    Returns the (x, y, w, h) of the best candidate or None.
    """
    img = cv2.imread(image_path)
    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Simple thresholding - efficient and works well for high contrast line art like checkboxes
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    height, width = img.shape[:2]
    image_area = width * height
    
    candidates = []
    
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        aspect_ratio = float(w) / h
        
        # Filter based on properties
        
        # 1. Aspect ratio should be close to 1 (square)
        if not (0.8 < aspect_ratio < 1.2):
            continue
            
        # 2. Minimum absolute size (to avoid noise/text periods)
        if w < 20 or h < 20:
            continue

        # 3. Area relative to image size
        if not (0.001 * image_area < area < 0.05 * image_area):
            continue
            
        # 4. Solidity/Extent
        contour_area = cv2.contourArea(cnt)
        extent = contour_area / area
        if extent < 0.8: # Stricter extent to avoid complex shapes
            continue

        # 5. Content check - Checkboxes are usually empty (white/solid)
        # Crop margin to avoid border
        roi = gray[y+5:y+h-5, x+5:x+w-5] 
        if roi.size > 0:
            std_dev = np.std(roi)
            if std_dev > 35.0: # High variance means complex content (image)
                continue

        candidates.append((x, y, w, h))
        
    # Heuristic: Checkbox captchas typically have 1 single checkbox.
    # If we find many candidates, it's likely a grid or something else.
    if len(candidates) > 2:
        return None
        
    if not candidates:
        return None
        
    # Return the largest candidate (most likely the main checkbox)
    best_candidate = max(candidates, key=lambda c: c[2] * c[3])
    return best_candidate

