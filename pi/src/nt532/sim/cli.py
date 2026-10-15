"""Tùy chọn dòng lệnh chung cho các script dựng sa bàn ảo: --camera, --look-at, --layout."""

import argparse

from .scene import CAMERA_PRESETS, DEFAULT_PRESET, NODE_LAYOUTS

DEFAULT_LOOK_AT = (0.60, 0.40, 0.20)  # điểm nhìn khi chỉ cho vị trí camera


def add_sim_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--camera", default=None, metavar="PRESET|x,y,z",
                   help=f"chỉ cho sa bàn ảo: vị trí camera, một preset ({', '.join(CAMERA_PRESETS)}) hoặc "
                        f"x,y,z (m); mặc định {DEFAULT_PRESET}")
    p.add_argument("--look-at", default=None, metavar="x,y,z",
                   help="chỉ cho sa bàn ảo: điểm camera nhìn vào (mặc định theo preset, hoặc "
                        f"{','.join(str(v) for v in DEFAULT_LOOK_AT)} khi --camera là x,y,z)")
    p.add_argument("--layout", choices=list(NODE_LAYOUTS), default=None,
                   help="chỉ cho sa bàn ảo: bố cục hai node (corners: hai góc trước bàn, mặc định; "
                        "posts: hai trụ sát bảng, bố cục cũ)")


def _xyz(text: str, what: str) -> tuple[float, float, float]:
    try:
        v = tuple(float(c) for c in text.split(","))
    except ValueError:
        v = ()
    if len(v) != 3:
        raise ValueError(f"{what} cần dạng x,y,z (m), nhận {text!r}")
    return v


def camera_spec(camera: str | None, look_at: str | None):
    """(--camera, --look-at) thành giá trị cho `SimWorld(camera=...)`: None, tên preset hoặc
    (vị trí, điểm nhìn). Báo ValueError với câu tiếng Việt nếu sai."""
    if camera is None and look_at is None:
        return None
    if camera is not None and camera in CAMERA_PRESETS and look_at is None:
        return camera
    if camera is None or camera in CAMERA_PRESETS:
        pos, target = CAMERA_PRESETS[camera or DEFAULT_PRESET]
    else:
        pos, target = _xyz(camera, "--camera"), DEFAULT_LOOK_AT
    return pos, _xyz(look_at, "--look-at") if look_at is not None else target


def sim_options(args, p: argparse.ArgumentParser | None = None) -> dict:
    """{"camera": ..., "node_layout": ...} từ kết quả parse_args, cho `build_sim`/`SimWorld`."""
    try:
        return {"camera": camera_spec(args.camera, args.look_at), "node_layout": args.layout}
    except ValueError as e:
        if p is None:
            raise
        p.error(str(e))
