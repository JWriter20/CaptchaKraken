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
    # ANIMATED CHALLENGES ONLY. Path to the keyframe the model chose to act on, and
    # its 1-based number in the set that was sent.
    #
    # Why an action carries a picture: on an animated puzzle the target is only
    # present part of the time, so these coordinates are correct only while the
    # widget looks the way it did in that keyframe. The driver therefore holds the
    # mouse until the live neighbourhood around the click point matches the same
    # neighbourhood of this file, then clicks. Without it the click fires on
    # whatever happens to be on screen, which for a fading sprite is usually
    # background.
    #
    # Absent on every still puzzle, where there is no moment to wait for. Both are
    # set together or not at all — a number with no image cannot be waited on, and
    # an image with no number cannot be reported.
    await_keyframe: Optional[str] = None
    frame: Optional[int] = None


class ClickAction(Action):
    action: Literal["click"]
    target_bounding_boxes: List[BoundingBox] # List of [x1, y1, x2, y2] in percentages


class DragAction(Action):
    action: Literal["drag"]
    # Optional, not merely defaulted: a PUZZLE-PIECE SLIDER is carried as a drag
    # with source_bounding_box explicitly None, and pydantic validates a value
    # that is passed even when it equals the default. Declaring these as bare
    # `BoundingBox = None` made constructing a slide raise ValidationError —
    # every slide puzzle, at the moment of acting on a correct answer.
    source_bounding_box: Optional[BoundingBox] = None  # [x1, y1, x2, y2] in percentages
    target_bounding_box: Optional[BoundingBox] = None  # [x1, y1, x2, y2] in percentages


class TypeAction(Action):
    """Type text into an input."""
    action: Literal["type"]
    text: str
    target_bounding_box: Optional[BoundingBox] = None  # [x1, y1, x2, y2] in percentages


class WaitAction(Action):
    """Wait for a specified duration."""
    action: Literal["wait"]
    duration_ms: int


class DoneAction(Action):
    """Signal that the captcha is solved or no further actions are needed."""
    action: Literal["done"]


CaptchaAction = Union[ClickAction, DragAction, TypeAction, WaitAction, DoneAction]
