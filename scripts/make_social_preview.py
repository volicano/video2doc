"""生成 GitHub 社交预览图（1280x640）。

用法：python scripts/make_social_preview.py
输出：assets/social-preview.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 640
S = 2  # 先按 2 倍画，再缩小，边缘更干净
FONTS = Path("C:/Windows/Fonts")
OUT = Path(__file__).resolve().parent.parent / "assets" / "social-preview.png"

BG_TOP = (7, 13, 26)
BG_BOTTOM = (15, 27, 51)
ACCENT = (56, 189, 248)
ACCENT_DARK = (2, 132, 199)
WHITE = (248, 250, 252)
MUTED = (148, 163, 184)
DIM = (100, 116, 139)

SANS = FONTS / "seguisb.ttf"
SANS_BOLD = FONTS / "segoeuib.ttf"
CJK = FONTS / "msyhbd.ttc"
MONO = FONTS / "consola.ttf"


def p(value: float) -> int:
    return round(value * S)


def font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), p(size))


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1) -> None:
    draw.rounded_rectangle(box, radius=p(radius), fill=fill, outline=outline, width=p(width))


def text_width(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=f)
    return box[2] - box[0]


def pill(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    f: ImageFont.FreeTypeFont,
    *,
    fill,
    color,
    outline=None,
    pad_x: float = 16,
    height: float = 40,
    radius: float | None = None,
) -> float:
    w = text_width(draw, text, f) + p(pad_x) * 2
    box = (p(x), p(y), p(x) + w, p(y) + p(height))
    rounded(draw, box, radius if radius is not None else height / 2, fill, outline, 1)
    draw.text((p(x + pad_x), p(y + height / 2)), text, font=f, fill=color, anchor="lm")
    return x + w / S


def linear_background() -> Image.Image:
    img = Image.new("RGB", (p(W), p(H)))
    draw = ImageDraw.Draw(img)
    for y in range(p(H)):
        t = y / (p(H) - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
        draw.line((0, y, p(W), y), fill=color)
    return img


def add_glow(img: Image.Image, cx: float, cy: float, r: float, color, alpha: int) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((p(cx - r), p(cy - r), p(cx + r), p(cy + r)), fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(p(r / 2.5)))
    img.alpha_composite(layer)


def add_grid(img: Image.Image) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(0, p(W), p(48)):
        d.line((x, 0, x, p(H)), fill=(148, 163, 184, 9), width=1)
    for y in range(0, p(H), p(48)):
        d.line((0, y, p(W), y), fill=(148, 163, 184, 9), width=1)
    img.alpha_composite(layer)


def add_document(img: Image.Image, d: ImageDraw.ImageDraw) -> None:
    x, y, w, h = 764, 96, 438, 436
    # 阴影
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        (p(x + 6), p(y + 24), p(x + w + 6), p(y + h + 24)),
        radius=p(24),
        fill=(0, 0, 0, 130),
    )
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(22))))

    rounded(d, (p(x), p(y), p(x + w), p(y + h)), 20, (248, 250, 252))
    d.rounded_rectangle(
        (p(x), p(y), p(x + w), p(y + 48)),
        radius=p(20),
        fill=(238, 242, 247),
    )
    d.rectangle((p(x), p(y + 32), p(x + w), p(y + 48)), fill=(238, 242, 247))
    d.line((p(x), p(y + 48), p(x + w), p(y + 48)), fill=(226, 232, 240), width=p(1))

    for i, color in enumerate(((248, 113, 113), (251, 191, 36), (52, 211, 153))):
        d.ellipse((p(x + 20 + i * 16), p(y + 19), p(x + 28 + i * 16), p(y + 27)), fill=color)
    d.text((p(x + 82), p(y + 24)), "output/BV1xx411c7mD/tutorial.html", font=font(SANS, 12), fill=DIM, anchor="lm")

    cx = x + 26
    d.text((p(cx), p(y + 72)), "CHAPTER 03 · 12:34", font=font(SANS_BOLD, 11), fill=ACCENT_DARK, anchor="lt")
    d.text((p(cx), p(y + 92)), "线程池与任务调度", font=font(CJK, 25), fill=(15, 23, 42), anchor="lt")

    for line_y, line_w in ((y + 138, 330), (y + 154, 280)):
        d.rounded_rectangle(
            (p(cx), p(line_y), p(cx + line_w), p(line_y + 7)),
            radius=p(3.5),
            fill=(226, 232, 240),
        )

    ix, iy, iw, ih = x + 26, y + 182, w - 52, 148
    rounded(d, (p(ix), p(iy), p(ix + iw), p(iy + ih)), 12, (11, 18, 32))
    d.text((p(ix + 18), p(iy + 16)), "ExecutorService", font=font(MONO, 12), fill=ACCENT, anchor="lt")
    bars = ((54, 168, (56, 189, 248)), (72, 224, (148, 163, 184)), (90, 132, (251, 191, 36)), (108, 196, (148, 163, 184)))
    for off, bw, color in bars:
        d.rounded_rectangle(
            (p(ix + 18), p(iy + off), p(ix + 18 + bw), p(iy + off + 7)),
            radius=p(3.5),
            fill=color,
        )
    # 时间轴
    ty = iy + ih - 24
    d.line((p(ix + 18), p(ty), p(ix + iw - 18), p(ty)), fill=(51, 65, 85), width=p(2))
    for fx in (0.15, 0.45, 0.72):
        px = ix + 18 + (iw - 36) * fx
        d.ellipse((p(px - 4), p(ty - 4), p(px + 4), p(ty + 4)), fill=ACCENT)

    # 图片上的时间戳胶囊
    tag = "12:34"
    tw = text_width(d, tag, font(SANS, 11)) + p(38)
    d.rounded_rectangle(
        (p(ix + 12), p(iy + ih - 34), p(ix + 12 + tw), p(iy + ih - 10)),
        radius=p(12),
        fill=(2, 6, 23),
        outline=(51, 65, 85),
        width=p(1),
    )
    cy_tag = iy + ih - 22
    d.polygon(
        ((p(ix + 20), p(cy_tag - 5)), (p(ix + 20), p(cy_tag + 5)), (p(ix + 27), p(cy_tag))),
        fill=WHITE,
    )
    d.text((p(ix + 34), p(cy_tag)), tag, font=font(SANS, 11), fill=WHITE, anchor="lm")

    for line_y, line_w in ((y + 352, 316), (y + 368, 252), (y + 384, 172)):
        d.rounded_rectangle(
            (p(cx), p(line_y), p(cx + line_w), p(line_y + 7)),
            radius=p(3.5),
            fill=(226, 232, 240),
        )

    # 悬浮的原片时间戳
    fx, fy, fw, fh = 620, 292, 176, 46
    shadow2 = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd2 = ImageDraw.Draw(shadow2)
    sd2.rounded_rectangle((p(fx + 4), p(fy + 10), p(fx + fw + 4), p(fy + fh + 10)), radius=p(23), fill=(0, 0, 0, 120))
    img.alpha_composite(shadow2.filter(ImageFilter.GaussianBlur(p(12))))
    d.rounded_rectangle(
        (p(fx), p(fy), p(fx + fw), p(fy + fh)),
        radius=p(23),
        fill=(9, 15, 30),
        outline=(51, 65, 85),
        width=p(1),
    )
    d.ellipse((p(fx + 14), p(fy + 13), p(fx + 34), p(fy + 33)), fill=ACCENT)
    d.polygon(
        ((p(fx + 22), p(fy + 18)), (p(fx + 22), p(fy + 28)), (p(fx + 29), p(fy + 23))),
        fill=(9, 15, 30),
    )
    d.text((p(fx + 44), p(fy + fh / 2)), "原片 12:34", font=font(CJK, 15), fill=WHITE, anchor="lm")


def main() -> None:
    img = linear_background().convert("RGBA")
    add_glow(img, 1060, 80, 380, (37, 99, 235), 70)
    add_glow(img, 160, 620, 320, (14, 165, 233), 34)
    add_grid(img)
    d = ImageDraw.Draw(img)

    # 左上角小标签
    pill(
        d,
        80,
        66,
        "OPEN SOURCE · MIT",
        font(SANS_BOLD, 11),
        fill=(15, 27, 51),
        color=ACCENT,
        outline=(30, 58, 95),
        pad_x=14,
        height=30,
    )

    # 标题
    title_font = font(SANS_BOLD, 94)
    d.text((p(76), p(112)), "video", font=title_font, fill=WHITE, anchor="lt")
    vw = text_width(d, "video", title_font)
    d.text((p(76) + vw, p(112)), "2doc", font=title_font, fill=ACCENT, anchor="lt")

    d.text((p(80), p(238)), "把视频，变成可读的教程", font=font(CJK, 33), fill=(226, 232, 240), anchor="lt")
    d.text(
        (p(80), p(288)),
        "Turn videos into illustrated, timestamped HTML docs",
        font=font(SANS, 19),
        fill=MUTED,
        anchor="lt",
    )

    d.rounded_rectangle((p(80), p(342), p(140), p(346)), radius=p(2), fill=ACCENT)

    # 特性标签
    chips = (("Bilibili / YouTube", 118), ("本地 / 云端 ASR", 132), ("单文件 HTML", 108))
    cx = 80
    for label, width in chips:
        d.rounded_rectangle(
            (p(cx), p(372), p(cx + width), p(408)),
            radius=p(18),
            fill=(15, 27, 51),
            outline=(30, 58, 95),
            width=p(1),
        )
        d.text((p(cx + width / 2), p(390)), label, font=font(CJK, 14), fill=(203, 213, 225), anchor="mm")
        cx += width + 10

    # 安装命令
    cmd = "$ pip install video2doc"
    cmd_font = font(MONO, 19)
    cmd_w = text_width(d, cmd, cmd_font) + p(36)
    d.rounded_rectangle(
        (p(80), p(446), p(80) + cmd_w, p(446 + 48)),
        radius=p(10),
        fill=(9, 15, 30),
        outline=(51, 65, 85),
        width=p(1),
    )
    d.text((p(98), p(470)), cmd, font=cmd_font, fill=(226, 232, 240), anchor="lm")
    d.text((p(80 + cmd_w / S + 18), p(470)), "Python 3.11+ · MIT", font=font(SANS, 15), fill=DIM, anchor="lm")

    d.text((p(80), p(560)), "github.com/volicano/video2doc", font=font(SANS, 14), fill=(120, 135, 155), anchor="lt")

    add_document(img, d)

    final = img.convert("RGB").resize((W, H), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    final.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
