from typing import List, Literal, Optional, Union, Tuple

from pydantic import BaseModel, RootModel


class BoundingBox(RootModel):
    root: Tuple[float, float, float, float]

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, item):
        return self.root[item]

    def __len__(self):
        return len(self.root)


class Action(BaseModel):
    action: str
    await_keyframe: Optional[str] = None
    frame: Optional[int] = None


class ClickAction(Action):
    action: Literal["click"]
    target_bounding_boxes: List[BoundingBox]


class DragAction(Action):
    action: Literal["drag"]
    source_bounding_box: Optional[BoundingBox] = None
    target_bounding_box: Optional[BoundingBox] = None


class TypeAction(Action):
    action: Literal["type"]
    text: str
    target_bounding_box: Optional[BoundingBox] = None


class WaitAction(Action):
    action: Literal["wait"]
    duration_ms: int


class DoneAction(Action):
    action: Literal["done"]


CaptchaAction = Union[ClickAction, DragAction, TypeAction, WaitAction, DoneAction]
