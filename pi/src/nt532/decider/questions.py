"""Câu hỏi và các lựa chọn, mỗi lựa chọn là một câu mô tả để nhúng bằng phía văn bản."""

QUESTIONS: dict[str, dict[str, str]] = {
    "fire_class": {
        "A": "a photo of a fire burning solid combustible materials such as wood, paper or cloth",
        "B": "a photo of a fire of flammable liquids or gases such as gasoline or a gas flame",
        "C": "a photo of a fire involving electrical equipment, wires or appliances",
        "D": "a photo of a fire burning combustible metal",
        "F": "a photo of a cooking oil or grease fire in a kitchen pan",
        "none": "a photo of a scene with no fire",
    },
    "real_fire": {
        "yes": "a photo of a real fire burning",
        "no": "a photo of a scene with no real fire",
    },
}


def candidate_texts(qid: str, prefix: str = "") -> tuple[list[str], list[str]]:
    """(danh sách lựa chọn, danh sách câu mô tả đã gắn tiền tố tác vụ nếu có)."""
    options = QUESTIONS[qid]
    return list(options), [prefix + t for t in options.values()]
