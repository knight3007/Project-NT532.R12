"""Biểu đồ SVG viết tay (không cần matplotlib). Màu lấy từ biến CSS của trang nên tự đổi theo chế độ sáng/tối."""

import math
from html import escape

W, H = 560, 300
PAD_L, PAD_R, PAD_T, PAD_B = 52, 14, 14, 38
SERIES = ("var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)")


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t, out = (lo // step) * step, []
    while t <= hi + step * 0.5:
        out.append(round(t, 10))
        t += step
    return out


def _fmt(v: float) -> str:
    return f"{v:g}"


class Plot:
    """Khung trục: x0..x1, y0..y1 → toạ độ ảnh."""

    def __init__(self, x_range, y_range, xlabel="", ylabel="", equal=False, w=W, h=H, xticks=True):
        self.w, self.h, self.xticks = w, h, xticks
        (self.x0, self.x1), (self.y0, self.y1) = x_range, y_range
        if equal:  # cùng tỉ lệ hai trục: nới khoảng ngắn hơn
            pw, ph = w - PAD_L - PAD_R, h - PAD_T - PAD_B
            sx, sy = (self.x1 - self.x0) / pw, (self.y1 - self.y0) / ph
            s = max(sx, sy)
            cx, cy = (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2
            self.x0, self.x1 = cx - s * pw / 2, cx + s * pw / 2
            self.y0, self.y1 = cy - s * ph / 2, cy + s * ph / 2
        self.parts: list[str] = []
        self.xlabel, self.ylabel = xlabel, ylabel
        self.legend: list[tuple[str, str]] = []

    def px(self, x: float) -> float:
        return PAD_L + (x - self.x0) / ((self.x1 - self.x0) or 1) * (self.w - PAD_L - PAD_R)

    def py(self, y: float) -> float:
        return self.h - PAD_B - (y - self.y0) / ((self.y1 - self.y0) or 1) * (self.h - PAD_T - PAD_B)

    def add(self, s: str) -> None:
        self.parts.append(s)

    def grid(self) -> str:
        out = []
        for t in _nice_ticks(self.x0, self.x1):
            if self.xticks and self.x0 <= t <= self.x1:
                x = self.px(t)
                out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{PAD_T}" y2="{self.h - PAD_B}"/>'
                           f'<text class="tick" x="{x:.1f}" y="{self.h - PAD_B + 14}" text-anchor="middle">{_fmt(t)}</text>')
        for t in _nice_ticks(self.y0, self.y1):
            if self.y0 <= t <= self.y1:
                y = self.py(t)
                out.append(f'<line class="grid" x1="{PAD_L}" x2="{self.w - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>'
                           f'<text class="tick" x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(t)}</text>')
        out.append(f'<text class="axis" x="{(PAD_L + self.w - PAD_R) / 2:.0f}" y="{self.h - 4}" '
                   f'text-anchor="middle">{escape(self.xlabel)}</text>')
        out.append(f'<text class="axis" transform="translate(12 {(PAD_T + self.h - PAD_B) / 2:.0f}) rotate(-90)" '
                   f'text-anchor="middle">{escape(self.ylabel)}</text>')
        return "".join(out)

    def dot(self, x, y, color, r=3.5, title=""):
        t = f"<title>{escape(title)}</title>" if title else ""
        self.add(f'<circle cx="{self.px(x):.1f}" cy="{self.py(y):.1f}" r="{r}" fill="{color}" fill-opacity=".75">{t}</circle>')

    def line(self, xs, ys, color, width=1.8, dash=""):
        pts = " ".join(f"{self.px(x):.1f},{self.py(y):.1f}" for x, y in zip(xs, ys, strict=True))
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<polyline fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{d} points="{pts}"/>')

    def hline(self, y, label="", color="var(--bad)"):
        if self.y0 <= y <= self.y1:
            self.add(f'<line x1="{PAD_L}" x2="{self.w - PAD_R}" y1="{self.py(y):.1f}" y2="{self.py(y):.1f}" '
                     f'stroke="{color}" stroke-dasharray="5 4"/>'
                     f'<text class="tick" x="{self.w - PAD_R - 2}" y="{self.py(y) - 4:.1f}" text-anchor="end">{escape(label)}</text>')

    def svg(self) -> str:
        leg = ""
        if len(self.legend) > 1:
            items = "".join(f'<span><i style="background:{c}"></i>{escape(n)}</span>' for n, c in self.legend)
            leg = f'<div class="legend">{items}</div>'
        return (f'<figure><svg viewBox="0 0 {self.w} {self.h}" role="img" xmlns="http://www.w3.org/2000/svg">'
                f"{self.grid()}{''.join(self.parts)}</svg>{leg}</figure>")


def _range(vals, pad=0.08, floor=None):
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    m = (hi - lo) * pad
    lo -= m
    hi += m
    return (lo if floor is None else max(floor, lo)), hi


def scatter(groups: dict[str, list[tuple[float, float]]], xlabel="", ylabel="", gate: float | None = None) -> str:
    """Điểm theo nhóm; `gate` vẽ vòng tròn bán kính gate quanh gốc (đơn vị trục), trục cùng tỉ lệ."""
    pts = [p for g in groups.values() for p in g]
    lim = max([abs(v) for p in pts for v in p] + [gate or 0, 1e-9]) * 1.15
    pl = Plot((-lim, lim), (-lim, lim), xlabel, ylabel, equal=True)
    if gate:
        pl.add(f'<ellipse cx="{pl.px(0):.1f}" cy="{pl.py(0):.1f}" rx="{abs(pl.px(gate) - pl.px(0)):.1f}" '
               f'ry="{abs(pl.py(gate) - pl.py(0)):.1f}" fill="none" stroke="var(--bad)" stroke-dasharray="5 4"/>')
    pl.add(f'<line class="axisline" x1="{pl.px(0):.1f}" x2="{pl.px(0):.1f}" y1="{PAD_T}" y2="{pl.h - PAD_B}"/>'
           f'<line class="axisline" x1="{PAD_L}" x2="{pl.w - PAD_R}" y1="{pl.py(0):.1f}" y2="{pl.py(0):.1f}"/>')
    for i, (name, g) in enumerate(groups.items()):
        c = SERIES[i % len(SERIES)]
        pl.legend.append((name, c))
        for x, y in g:
            pl.dot(x, y, c, title=f"{name}: ({x:.2f}, {y:.2f})")
    return pl.svg()


def lines(series: dict[str, tuple[list[float], list[float]]], xlabel="", ylabel="", gate: float | None = None,
          gate_label="") -> str:
    xs = [x for s in series.values() for x in s[0]]
    ys = [y for s in series.values() for y in s[1]]
    pl = Plot(_range(xs, 0.02), _range(ys + ([gate] if gate else []) + [0], 0.08), xlabel, ylabel)
    if gate:
        pl.hline(gate, gate_label)
    for i, (name, (x, y)) in enumerate(series.items()):
        c = SERIES[i % len(SERIES)]
        pl.legend.append((name, c))
        if len(x) == 1:
            pl.dot(x[0], y[0], c)
        else:
            pl.line(x, y, c)
            if len(x) <= 12:
                for a, b in zip(x, y, strict=True):
                    pl.dot(a, b, c, r=2.5)
    return pl.svg()


def bars(labels: list[str], values: list[float], ylabel="", ymax: float | None = None, unit="") -> str:
    """Cột đứng; nhãn xoay khi nhiều."""
    n = len(labels)
    w = max(W, 60 + n * 44)
    top = ymax if ymax is not None else max(values + [1e-9]) * 1.15
    pl = Plot((0, n), (0, top), "", ylabel, w=w, xticks=False)
    for i, (lab, v) in enumerate(zip(labels, values, strict=True)):
        x0, x1 = pl.px(i + 0.15), pl.px(i + 0.85)
        y = pl.py(v)
        pl.add(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{x1 - x0:.1f}" height="{pl.py(0) - y:.1f}" fill="var(--c1)" rx="2">'
               f"<title>{escape(lab)}: {v:g}{unit}</title></rect>"
               f'<text class="tick" x="{(x0 + x1) / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle">{v:g}</text>'
               f'<text class="tick" x="{(x0 + x1) / 2:.1f}" y="{pl.h - PAD_B + 14}" text-anchor="middle">{escape(lab)}</text>')
    return pl.svg()



def heat_grid(cells: dict[tuple[float, float], tuple[float, str]], xlabel="x (m)", ylabel="z (m)") -> str:
    """Lưới điểm: mỗi ô tô theo tỉ lệ 0..1 (ô thứ nhất của giá trị), chữ là nhãn. Khoá là (x, z)."""
    xs = sorted({k[0] for k in cells})
    zs = sorted({k[1] for k in cells}, reverse=True)
    cw, ch = 84, 56
    w, h = PAD_L + cw * len(xs) + PAD_R, PAD_T + ch * len(zs) + PAD_B
    out = []
    for r, z in enumerate(zs):
        out.append(f'<text class="tick" x="{PAD_L - 6}" y="{PAD_T + r * ch + ch / 2 + 4}" text-anchor="end">{z:g}</text>')
        for c, x in enumerate(xs):
            if r == 0:
                out.append(f'<text class="tick" x="{PAD_L + c * cw + cw / 2}" y="{h - PAD_B + 14}" text-anchor="middle">{x:g}</text>')
            cell = cells.get((x, z))
            if cell is None:
                continue
            frac, label = cell
            pct = round(frac * 100)
            out.append(
                f'<rect x="{PAD_L + c * cw + 2}" y="{PAD_T + r * ch + 2}" width="{cw - 4}" height="{ch - 4}" rx="4" '
                f'style="fill:color-mix(in srgb, var(--good) {pct}%, var(--bad))" fill-opacity=".85"/>'
                f'<text class="cell" x="{PAD_L + c * cw + cw / 2}" y="{PAD_T + r * ch + ch / 2 + 4}" text-anchor="middle">{escape(label)}</text>')
    out.append(f'<text class="axis" x="{(PAD_L + w - PAD_R) / 2:.0f}" y="{h - 4}" text-anchor="middle">{escape(xlabel)}</text>')
    out.append(f'<text class="axis" transform="translate(12 {(PAD_T + h - PAD_B) / 2:.0f}) rotate(-90)" text-anchor="middle">{escape(ylabel)}</text>')
    return f'<figure><svg viewBox="0 0 {w} {h}" role="img" xmlns="http://www.w3.org/2000/svg">{"".join(out)}</svg></figure>'
