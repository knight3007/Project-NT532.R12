"""Camera ảo có giao diện đủ giống cv2.VideoCapture cho code thị giác hiện có."""

import cv2
import numpy as np

from .scene import Scene


class SimCamera:
    """Mỗi `read` dựng một khung mới từ `scene`; sửa scene thì khung kế tiếp phản ánh ngay."""

    def __init__(self, scene: Scene | None = None, fps: float = 30.0) -> None:
        self.scene = scene or Scene()
        self.fps = fps
        self.frames = 0
        self._open = True

    @classmethod
    def demo(cls) -> "SimCamera":
        """Scene mặc định kèm hai thẻ bia (nếu đã sinh ảnh thẻ) để các script có cái mà nhìn."""
        scene = Scene()
        try:
            scene.add_card(0.30, 0.25)
            scene.add_card(0.90, 0.35)
        except FileNotFoundError:
            pass
        return cls(scene)

    def isOpened(self) -> bool:  # giữ tên của cv2.VideoCapture
        return self._open

    def grab(self) -> bool:
        """Bỏ một khung (webcam thật bỏ khung cũ trong bộ đệm); không tốn công dựng ảnh."""
        if not self._open:
            return False
        self.frames += 1
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self._open:
            return False, None
        self.frames += 1
        return True, self.scene.render()

    def release(self) -> None:
        self._open = False

    def get(self, prop: int) -> float:
        width, height = self.scene.size
        return {
            cv2.CAP_PROP_FRAME_WIDTH: float(width),
            cv2.CAP_PROP_FRAME_HEIGHT: float(height),
            cv2.CAP_PROP_FRAME_COUNT: -1.0,  # nguồn trực tiếp, không có tổng số khung
            cv2.CAP_PROP_FPS: self.fps,
            cv2.CAP_PROP_POS_FRAMES: float(self.frames),
        }.get(prop, 0.0)

    def set(self, prop: int, value: float) -> bool:
        """Độ phân giải do intrinsics của scene quyết định; các thuộc tính khác bỏ qua."""
        return prop not in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT)
