import numpy as np

from .types import Detection


def detect(frame: np.ndarray) -> list[Detection]:
    """Tìm các thẻ bia trong ảnh BGR, sắp theo độ tin cậy giảm dần."""
    raise NotImplementedError
