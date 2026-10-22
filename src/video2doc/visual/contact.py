"""把抽帧、OCR、对齐结果摊成一张 preview.html，用来肉眼验收。

这不是最终产物，而是调参用的工作台：
- 上面是信息密度曲线，蓝点是被选中的帧，一眼能看出「为什么漏了这一段」
- 下面是每张图的 OCR 文字和这段的口播，直接判断图配得对不对
"""

from __future__ import annotations

import base64
import html
import io
from pathlib import Path

from PIL import Image

from ..schema import Alignment, FrameSelection, OCRResult, SourceInfo, Transcript
from ..utils.common import hms

_OCR_PREVIEW_CHARS = 500
_SPOKEN_PREVIEW_CHARS = 300

_CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb;
        --accent:#2563eb; --warn:#b45309; }
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:var(--bg); color:var(--ink);
       font:14px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
header { max-width:1280px; margin:0 auto 16px; }
h1 { font-size:20px; margin:0 0 4px; }
.meta { color:var(--muted); font-size:13px; }
.panel { max-width:1280px; margin:0 auto 20px; background:var(--card); border:1px solid var(--line);
         border-radius:10px; padding:14px 16px; }
.panel h2 { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
            margin:0 0 10px; font-weight:600; }
.chart { width:100%; height:auto; display:block; }
.legend { display:flex; gap:16px; font-size:12px; color:var(--muted); margin-top:6px; }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; }
.grid { max-width:1280px; margin:0 auto; display:grid; gap:14px;
        grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden;
        display:flex; flex-direction:column; }
.shot { background:#0f172a; aspect-ratio:16/9; display:flex;
        align-items:center; justify-content:center; }
.shot img { width:100%; height:100%; object-fit:contain; }
.body { padding:10px 12px 12px; display:flex; flex-direction:column; gap:6px; }
.row { display:flex; align-items:center; gap:8px; }
.t { font-weight:600; font-variant-numeric:tabular-nums; }
a.t { color:var(--accent); text-decoration:none; }
a.t:hover { text-decoration:underline; }
.badge { font-size:11px; padding:1px 7px; border-radius:999px; background:#eef2ff; color:#3730a3; }
.badge.face { background:#fef3c7; color:#92400e; }
.badge.code { background:#dcfce7; color:#166534; }
.badge.whiteboard { background:#e0f2fe; color:#075985; }
.score { margin-left:auto; font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
.range { font-size:11px; color:var(--muted); }
.label { font-size:11px; color:var(--muted); margin-bottom:2px; }
.ocr { font-family:ui-monospace, Consolas, monospace; font-size:12px; white-space:pre-wrap;
       background:#f9fafb; border:1px solid var(--line); border-radius:6px; padding:6px 8px;
       max-height:160px; overflow:auto; }
.ocr.empty { color:#9ca3af; font-family:inherit; font-style:italic; }
.spoken { font-size:12.5px; color:#374151; max-height:120px; overflow:auto; }
.suspect { color:var(--warn); }
footer { max-width:1280px; margin:24px auto 0; color:var(--muted); font-size:12px; }
"""


def render_debug_html(
    dest: Path,
    *,
    job_dir: Path,
    source: SourceInfo | None,
    selection: FrameSelection,
    ocr: OCRResult | None,
    alignment: Alignment,
    transcript: Transcript | None,
) -> Path:
    texts = {frame.index: frame for frame in ocr.frames} if ocr else {}
    title = source.title if source and source.title else "video2doc preview"
    link = _video_link(source)

    cards = "\n".join(
        _card(moment, texts.get(moment.index), job_dir, link) for moment in alignment.moments
    )
    if not cards:
        cards = '<p class="meta">没有选中任何画面。</p>'

    ocr_note = ocr.summary() if ocr else "未启用"
    header_meta = " · ".join(
        [
            f"时长 {hms(selection.duration)}",
            f"采样 {selection.sampled} 帧",
            f"{selection.shots} 个镜头",
            f"选中 {len(selection.frames)} 张",
            f"OCR {ocr_note}",
            f"覆盖 {alignment.coverage:.0%}",
        ]
    )
    if transcript is not None:
        header_meta += f" · 文本 {transcript.char_count} 字"

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · preview</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <div class="meta">{html.escape(header_meta)}</div>
</header>

<section class="panel">
  <h2>信息密度曲线</h2>
  {_chart(selection)}
  <div class="legend">
    <span><i class="dot" style="background:#2563eb"></i>边缘密度（画面信息量）</span>
    <span><i class="dot" style="background:#ef4444"></i>肤色占比（口播镜头）</span>
    <span><i class="dot" style="background:#111827"></i>竖线 = 被选中的帧</span>
  </div>
</section>

<main class="grid">
{cards}
</main>

<footer>
  抽帧策略 {html.escape(selection.strategy)} · 采样间隔 {selection.interval:g}s
  · 这是调参用的调试页面，不是最终教程。
</footer>
</body>
</html>
"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html_text, encoding="utf-8")
    return dest


# ------------------------------------------------------------------ 组件


def _card(
    moment,
    frame_text,
    job_dir: Path,
    link: str | None,
) -> str:
    image = _thumbnail(job_dir / moment.image)
    shot = (
        f'<img src="{image}" alt="画面 {html.escape(hms(moment.time))}">'
        if image
        else '<span class="meta">图片缺失</span>'
    )

    time_html = html.escape(hms(moment.time))
    if link:
        stamp = int(moment.time)
        href = f"{link}{'&' if '?' in link else '?'}t={stamp}"
        time_html = f'<a class="t" href="{html.escape(href)}" target="_blank">{time_html}</a>'
    else:
        time_html = f'<span class="t">{time_html}</span>'

    extended = '<span class="range">语音取自邻近片段</span>' if moment.extended else ""

    if frame_text and frame_text.text:
        text = frame_text.text[:_OCR_PREVIEW_CHARS]
        if len(frame_text.text) > _OCR_PREVIEW_CHARS:
            text += "\n…"
        ocr_block = (
            f'<div><div class="label">画面文字</div>'
            f'<div class="ocr">{html.escape(text)}</div></div>'
        )
    else:
        ocr_block = (
            '<div><div class="label">画面文字</div>'
            '<div class="ocr empty">（这一帧没有识别到文字）</div></div>'
        )

    if moment.spoken_text:
        spoken = moment.spoken_text
        clipped = len(spoken) > _SPOKEN_PREVIEW_CHARS
        if clipped:
            spoken = spoken[:_SPOKEN_PREVIEW_CHARS] + "…"
        spoken_block = (
            f'<div><div class="label">这段在讲</div>'
            f'<div class="spoken">{html.escape(spoken)}</div></div>'
        )
    else:
        spoken_block = '<div class="label suspect">这一段没有对应的口播</div>'

    span = f"{html.escape(hms(moment.start))} – {html.escape(hms(moment.end))}"

    return f"""<article class="card">
  <div class="shot">{shot}</div>
  <div class="body">
    <div class="row">
      {time_html}
      <span class="badge {html.escape(moment.kind)}">{html.escape(moment.kind)}</span>
      <span class="score">score {moment.score:.3f}</span>
    </div>
    <div class="range">{span} {extended}</div>
    {ocr_block}
    {spoken_block}
  </div>
</article>"""


def _chart(selection: FrameSelection) -> str:
    width, height = 1000.0, 170.0
    points = selection.curve
    if not points:
        return '<p class="meta">没有采样数据。</p>'

    duration = selection.duration or points[-1].t or 1.0
    if len(points) > 1400:
        step = len(points) // 1400 + 1
        points = points[::step]

    def polyline(attr: str, color: str) -> str:
        coords = " ".join(
            f"{point.t / duration * width:.1f},{height - getattr(point, attr) * height:.1f}"
            for point in points
        )
        return (
            f'<polyline fill="none" stroke="{color}" stroke-width="1.4" '
            f'stroke-opacity=".85" points="{coords}"/>'
        )

    marks = "".join(
        f'<line x1="{frame.time / duration * width:.1f}" y1="0" '
        f'x2="{frame.time / duration * width:.1f}" y2="{height}" '
        f'stroke="#111827" stroke-opacity=".28" stroke-width="1"/>'
        f'<circle cx="{frame.time / duration * width:.1f}" '
        f'cy="{height - min(1.0, max(0.0, frame.score)) * height:.1f}" '
        f'r="3" fill="#111827"/>'
        for frame in selection.frames
    )

    grid = "".join(
        f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height}" stroke="#e5e7eb" stroke-width="1"/>'
        for x in (width * index / 6 for index in range(7))
    )
    labels = "".join(
        f'<text x="{width * index / 6 + 3:.1f}" y="{height - 5:.1f}" font-size="11" '
        f'fill="#9ca3af">{html.escape(hms(duration * index / 6))}</text>'
        for index in range(7)
    )

    return (
        f'<svg class="chart" viewBox="0 0 {width:.0f} {height + 12:.0f}" '
        f'preserveAspectRatio="none" role="img">'
        f"{grid}{polyline('edge', '#2563eb')}{polyline('skin', '#ef4444')}"
        f"{marks}{labels}</svg>"
    )


def _thumbnail(path: Path, width: int = 480) -> str:
    if not path.exists():
        return ""
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            if rgb.width > width:
                ratio = width / rgb.width
                rgb = rgb.resize((width, max(1, round(rgb.height * ratio))), Image.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, "WEBP", quality=72, method=4)
    except OSError:
        return ""
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{payload}"


def _video_link(source: SourceInfo | None) -> str | None:
    if source is None or source.kind != "url":
        return None
    return source.webpage_url or source.url
