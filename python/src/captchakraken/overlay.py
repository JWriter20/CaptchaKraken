"""Numbered box overlays. The grid overlay the model reads is drawn here, so its look is frozen."""

from typing import Any, Dict

from PIL import Image, ImageDraw, ImageFont

from .kinds import LabelPosition

_FONT_CACHE: Dict[int, Any] = {}
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "arial.ttf",
    "Arial Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int):
    if size not in _FONT_CACHE:
        _FONT_CACHE[size] = None
        for path in _FONT_PATHS:
            try:
                _FONT_CACHE[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
    return _FONT_CACHE[size]


def _rgba(hex_color: str, alpha: int):
    hex_color = hex_color.lstrip("#")
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4)) if len(hex_color) == 6 else (255, 0, 0)
    return rgb + (alpha,)


def _draw_box(draw, bbox, label, image_size, color, label_position, box_style):
    x1, y1, x2, y2 = bbox
    if box_style == "solid":
        draw.rectangle([x1, y1, x2, y2], outline=(0, 0, 0, 210), width=4)
    draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
    if not label:
        return
    img_w, img_h = image_size
    font = _font(max(14, min(48, int(img_w * 0.035))))
    tb = draw.textbbox((0, 0), label, font=font) if font else draw.textbbox((0, 0), label)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    cw, ch = tw + 4, th + 4
    if label_position is LabelPosition.CENTER:
        bx1, by1 = (x1 + x2) / 2 - cw / 2, (y1 + y2) / 2 - ch / 2
    elif label_position is LabelPosition.BOTTOM_RIGHT:
        bx1, by1 = x2 - 4 - cw, y2 - 4 - ch
    elif label_position is LabelPosition.BOTTOM_LEFT:
        bx1, by1 = x1 + 4, y2 - 4 - ch
    elif label_position is LabelPosition.TOP_RIGHT:
        bx1, by1 = x2 - 4 - cw, y1 + 4
    else:
        bx1, by1 = x1 + 4, y1 + 4
    bx1 = min(max(bx1, 0), img_w - cw)
    by1 = min(max(by1, 0), img_h - ch)
    tx, ty = bx1 + (cw - tw) // 2, by1 + (ch - th) // 2 - tb[1]
    draw.rectangle([bx1, by1, bx1 + cw, by1 + ch], fill=_rgba(color, 200), outline=color, width=2)
    draw.text((tx, ty), label, fill="white", font=font, stroke_width=2, stroke_fill="black")


def add_overlays_to_image(image_path: str, boxes: list, output_path: str = None,
                          label_position: LabelPosition = LabelPosition.TOP_LEFT):
    """Draw each box (normalised [x1,y1,x2,y2] or pixel [x,y,w,h]) with its number/text label and save."""
    label_position = LabelPosition(label_position)
    with Image.open(image_path) as img:
        img = img.convert("RGBA")
        draw = ImageDraw.Draw(img)
        width, height = img.size
        for box in boxes:
            bbox = box["bbox"]
            if all(v <= 1.0 for v in bbox):
                coords = (bbox[0] * width, bbox[1] * height, bbox[2] * width, bbox[3] * height)
            else:
                x, y, w, h = bbox
                coords = (x, y, x + w, y + h)
            label = " ".join(str(v) for v in (box.get("number"), box.get("text")) if v not in (None, ""))
            _draw_box(draw, coords, label, img.size, box.get("color", "#FF6B6B"), label_position,
                      box.get("box_style") or box.get("style") or "thin")
        # RGBA cannot be saved as JPEG; the failure would surface a stage later.
        img.convert("RGB").save(output_path or image_path)
