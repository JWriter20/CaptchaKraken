from typing import List, Literal, Optional, Union, Tuple

from pydantic import BaseModel, RootModel


class BoundingBox(RootModel):
    """
    Strongly typed bounding box: [x1, y1, x2, y2] in percentages (0.0 to 1.0).
    Acts like a list for convenience but ensures exactly 4 float elements.
    """
    root: Tuple[float, float, float, float]

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, item):
        return self.root[item]

    def __len__(self):
        return len(self.root)


class Action(BaseModel):
    action: str


class ClickAction(Action):
    action: Literal["click"]
    target_bounding_boxes: List[BoundingBox] # List of [x1, y1, x2, y2] in percentages


class DragAction(Action):
    action: Literal["drag"]
    source_bounding_box: BoundingBox = None  # [x1, y1, x2, y2] in percentages
    target_bounding_box: BoundingBox = None  # [x1, y1, x2, y2] in percentages


class TypeAction(Action):
    """Type text into an input."""
    action: Literal["type"]
    text: str
    target_bounding_box: BoundingBox = None  # [x1, y1, x2, y2] in percentages


class WaitAction(Action):
    """Wait for a specified duration."""
    action: Literal["wait"]
    duration_ms: int


class DoneAction(Action):
    """Signal that the captcha is solved or no further actions are needed."""
    action: Literal["done"]


CaptchaAction = Union[ClickAction, DragAction, TypeAction, WaitAction, DoneAction]
