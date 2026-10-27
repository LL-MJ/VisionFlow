from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FrameInfo:
    frame_index: int
    timestamp_ms: int
    width: int
    height: int


class Detector(Protocol):
    def detect(self, frame: FrameInfo) -> list[dict]: ...


class FakeDetector:
    """Produces a synthetic full-frame box; it does not inspect pixels or run a model."""

    def detect(self, frame: FrameInfo) -> list[dict]:
        return [{"class_id": 0, "class_name": "fake_object", "confidence": 0.5,
                 "bbox_xyxy": [0, 0, frame.width, frame.height]}]
