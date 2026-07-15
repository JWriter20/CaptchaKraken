from typing import List, Literal, Optional, Union, Dict
from pydantic import BaseModel, Field, model_validator


class PlannerAction(BaseModel):
    goal: Optional[str] = Field(None, description="Description of the visual goal")


class PlannerClickAction(PlannerAction):
    action: Literal["click"]
    target_ids: List[int] = Field(description="List of object IDs or grid cell numbers to click")


class PlannerDragAction(PlannerAction):
    action: Literal["drag"]
    source_id: int = Field(description="Object ID of item to drag")
    target_id: int = Field(description="Object ID of destination")


class PlannerTypeAction(PlannerAction):
    action: Literal["type"]
    text: str = Field(description="Text to type into the input")
    target_id: int = Field(description="Object ID of the input field")


class PlannerWaitAction(PlannerAction):
    action: Literal["wait"]
    duration_ms: int = Field(default=500, description="Duration to wait in milliseconds")


class PlannerDoneAction(PlannerAction):
    action: Literal["done"]


class DragGuess(BaseModel):
    Action: Literal["simulate_drag"]
    SourceID: int
    DestinationDescription: str
    EstimatedPosition: Dict[str, int] = Field(description="{'x': 1-1000, 'y': 1-1000}")


class DragRefineResult(BaseModel):
    SourceId: int
    estimatedVerticalDistanceFromTarget: int = Field(description="negative = above, positive = below")
    estimatedHorizontalDistanceFromTarget: int = Field(description="negative = left, positive = right")


class PlannerDragRefineAction(PlannerAction):
    # This is for Step 3: Verification and adjustment
    output: List[DragRefineResult]


class PlannerToolCall(BaseModel):
    name: Literal["detect", "simulate_drag"]
    args: dict


class PlannerPlan(BaseModel):
    goal: Optional[str] = None
    tool_calls: Optional[List[PlannerToolCall]] = None
    # Step 1: Detect all draggable items
    objectDescription: Optional[str] = None
    # Step 2: Initial guess (general LoRa) uses output
    output: Optional[List[DragGuess]] = None
    # We allow the planner to return either a direct action or tool calls or new output format
    action: Optional[Union[PlannerClickAction, PlannerDragAction, PlannerTypeAction, PlannerWaitAction, PlannerDoneAction]] = None

    @model_validator(mode="after")
    def check_exclusive(self) -> "PlannerPlan":
        # At least one must be provided
        if not any([self.action, self.tool_calls, self.output, self.objectDescription]):
            raise ValueError("Provide EITHER 'action', 'tool_calls', 'output', OR 'objectDescription'.")
        return self

