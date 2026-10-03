from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import REPO_ROOT
from .types import Detection

DEFAULT_WEIGHTS = REPO_ROOT / "models" / "fire-n.pt"


@lru_cache(maxsize=4)
def _load(weights: str):
    from ultralytics import YOLO  # nặng, chỉ nạp khi cần

    model = YOLO(weights)
    fire = next(i for i, name in model.names.items() if name == "fire")
    return model, fire


def detect(
    frame: np.ndarray,
    weights: str | Path = DEFAULT_WEIGHTS,
    conf: float = 0.25,
    imgsz: int = 640,
    device: str | None = None,
) -> list[Detection]:
    """Tìm lửa trong ảnh BGR, sắp theo độ tin cậy giảm dần. Bỏ qua lớp khói."""
    model, fire = _load(str(weights))
    result = model.predict(
        frame, conf=conf, imgsz=imgsz, classes=[fire], device=device, verbose=False
    )[0]
    boxes = zip(result.boxes.xywh.tolist(), result.boxes.conf.tolist())
    found = [Detection(u, v, w, h, c) for (u, v, w, h), c in boxes]
    return sorted(found, key=lambda d: d.conf, reverse=True)
