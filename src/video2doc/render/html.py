"""把写作产物渲染成单页 HTML（以及一份可编辑的 Markdown）。

渲染层只做三件事：排版、把 `[[img:N]]` 换成真图、把时间换算成可点的链接。
这里不调用任何模型，同样的输入永远得到同样的页面。
"""

from __future__ import annotations

import base64
import html
import io
import re
from pathlib import Path

from markdown_it import MarkdownIt
from PIL import Image

from ..config import RenderConfig
from ..schema import Critique, Document, ImageRef, SourceInfo, WrittenSection
from ..utils.common import hms, now_iso
from ..utils.markdown import shift_headings

IMG_RE = re.compile(r"\[\[img:(\d+)\]\]", re.IGNORECASE)
_MAX_IMAGE_WIDTH = 1280
_IMAGE_QUALITY = 78

KIND_LABELS = {
    "concept": "概念",
    "example": "示例",
    "qa": "问答",
    "summary": "小结",
    "setup": "准备",
    "other": "其他",
}

_MD = MarkdownIt(
    "commonmark",
    {"html": False, "linkify": False, "typographer": False},
).enable("table")

_CSS = """
:root {
  --bg:#ffffff; --ink:#1f2328; --muted:#656d76; --line:#d8dee4; --soft:#f6f8fa;
  --accent:#0969da; --measure:44rem; --nav:15rem;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#0d1117; --ink:#e6edf3; --muted:#9198a1; --line:#30363d; --soft:#161b22;
    --accent:#4493f8;
  }
}
:root[data-theme="dark"] {
  --bg:#0d1117; --ink:#e6edf3; --muted:#9198a1; --line:#30363d; --soft:#161b22;
  --accent:#4493f8;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:16px/1.75 -apple-system, "Segoe UI", "Microsoft YaHei", "Noto Sans SC", sans-serif;
       -webkit-font-smoothing:antialiased; }
a { color:var(--accent); }
.wrap { max-width:72rem; margin:0 auto; padding:0 1.5rem; }
header.top { padding:3rem 0 1.6rem; border-bottom:1px solid var(--line); }
.eyebrow { font-size:.75rem; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
h1 { font-size:clamp(1.6rem, 3vw, 2.1rem); line-height:1.3; margin:.5rem 0 .8rem; }
.lede { font-size:1.02rem; color:var(--muted); max-width:var(--measure); margin:0; }
.chips { display:flex; flex-wrap:wrap; gap:.45rem; margin-top:1.2rem; }
.chip { font-size:.76rem; color:var(--muted); border:1px solid var(--line);
        border-radius:999px; padding:.12rem .62rem; }
.chip a { color:inherit; text-decoration:none; }
.chip a:hover { color:var(--accent); }
.layout { display:grid; grid-template-columns:var(--nav) minmax(0,1fr);
          gap:2.5rem; padding:2.2rem 0 3rem; }
nav.toc { position:sticky; top:1.5rem; align-self:start; font-size:.88rem; }
nav.toc .label { font-size:.72rem; letter-spacing:.12em; text-transform:uppercase;
                 color:var(--muted); margin-bottom:.6rem; }
nav.toc ol { list-style:none; margin:0; padding:0; }
nav.toc li { margin:.1rem 0; }
nav.toc a { display:flex; gap:.5rem; align-items:baseline; padding:.28rem .5rem;
            border-radius:6px; color:var(--muted); text-decoration:none; }
nav.toc a:hover { background:var(--soft); color:var(--ink); }
nav.toc .n { font-size:.75rem; color:var(--muted); font-variant-numeric:tabular-nums; }
nav.toc .time { margin-left:auto; font-size:.75rem; font-variant-numeric:tabular-nums;
                color:var(--muted); }
main { max-width:var(--measure); min-width:0; }
section.sec { padding:1.7rem 0 2rem; border-top:1px solid var(--line); }
section.sec:first-child { border-top:0; padding-top:0; }
h2 { font-size:1.42rem; line-height:1.35; margin:0 0 .45rem; scroll-margin-top:1rem; }
.sec-meta { font-size:.8rem; color:var(--muted); margin-bottom:1.1rem; }
.sec-meta a { color:var(--muted); text-decoration:none; }
.sec-meta a:hover { color:var(--accent); text-decoration:underline; }
h3 { font-size:1.08rem; margin:1.7rem 0 .5rem; }
h4 { font-size:1rem; margin:1.3rem 0 .4rem; }
p { margin:.85rem 0; }
ul, ol { padding-left:1.35rem; }
li { margin:.3rem 0; }
code { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:.88em;
       background:var(--soft); border-radius:4px; padding:.12em .34em; }
pre { background:var(--soft); border:1px solid var(--line); border-radius:8px;
      padding:.9rem 1rem; overflow:auto; font-size:.84rem; line-height:1.55; }
pre code { background:none; padding:0; font-size:inherit; }
blockquote { margin:1rem 0; padding:.35rem 1rem; border-left:3px solid var(--line);
             color:var(--muted); }
blockquote p { margin:.35rem 0; }
figure { margin:1.3rem 0; }
figure img { display:block; width:100%; height:auto; border:1px solid var(--line);
             border-radius:8px; background:#0f172a; }
figcaption { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:.45rem;
             font-size:.76rem; color:var(--muted); }
figcaption .ts { font-variant-numeric:tabular-nums; color:var(--ink); font-weight:600; }
figcaption a { color:var(--muted); }
table { border-collapse:collapse; width:100%; margin:1.1rem 0; font-size:.9rem; }
th, td { border:1px solid var(--line); padding:.4rem .6rem; text-align:left; }
th { background:var(--soft); }
hr { border:0; border-top:1px solid var(--line); margin:2rem 0; }
footer { border-top:1px solid var(--line); padding:1.5rem 0 3rem;
         color:var(--muted); font-size:.78rem; }
footer p { margin:.35rem 0; }
.note { color:var(--muted); }
@media (max-width: 900px) {
  .layout { grid-template-columns:1fr; gap:1.5rem; }
  nav.toc { position:static; order:2; border-top:1px solid var(--line); padding-top:1rem; }
  main { order:1; }
}
@media print {
  nav.toc { display:none; }
  .layout { display:block; }
  body { font-size:11.5pt; }
  figure { break-inside:avoid; }
  a { color:inherit; }
}
"""


def build_html(
    document: Document,
    source: SourceInfo | None,
    critique: Critique | None,
    *,
    job_dir: Path,
    config: RenderConfig,
    title: str | None = None,
) -> str:
    page_title = title or config.title or document.title or "视频教程"
    link = video_link(source)
    theme_attr = f' data-theme="{config.theme}"' if config.theme in ("light", "dark") else ""

    sections_html = "\n".join(
        _section_html(section, job_dir, link, config) for section in document.sections
    )
    toc = _toc(document) if config.show_toc and document.sections else ""
    chips = _chips(document, source, critique, link)
    intro = f'<p class="lede">{html.escape(document.intro)}</p>' if document.intro else ""
    footer = _footer(document, critique, source, page_title)

    return f"""<!doctype html>
<html lang="{html.escape(document.language or "zh")}"{theme_attr}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="video2doc">
<title>{html.escape(page_title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <div class="eyebrow">视频图文教程</div>
  <h1>{html.escape(page_title)}</h1>
  {intro}
  <div class="chips">{chips}</div>
</header>
<div class="layout">
{toc}
<main>
{sections_html}
</main>
</div>
<footer>
{footer}
</footer>
</div>
</body>
</html>
"""


def build_markdown(
    document: Document,
    source: SourceInfo | None,
    *,
    title: str | None = None,
    captions: bool = False,
) -> str:
    """导出一份 Markdown：图片用相对路径，方便继续手工编辑。"""
    page_title = title or document.title or "视频教程"
    link = video_link(source)
    meta = [hms(document.duration), f"{len(document.sections)} 节"]
    if link:
        meta.append(f"[原视频]({link})")
    meta.append(f"生成于 {now_iso()[:10]}")

    lines = [f"# {page_title}", "", "> " + " · ".join(meta), ""]
    if document.intro:
        lines += [document.intro, ""]

    if document.sections:
        lines += ["## 目录", ""]
        for index, section in enumerate(document.sections, 1):
            lines.append(f"{index}. {section.title} · {hms(section.start)}")
        lines.append("")

    for section in document.sections:
        lines += [f"## {section.title}", ""]
        label = KIND_LABELS.get(section.kind, section.kind)
        lines += [f"> {hms(section.start)} – {hms(section.end)} · {label}", ""]
        lines += [_markdown_body(section, captions=captions), ""]

    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------ 组件


def _markdown_body(section: WrittenSection, *, captions: bool = False) -> str:
    refs = {ref.id: ref for ref in section.images}

    def replace(match: re.Match[str]) -> str:
        ref = refs.get(int(match.group(1)))
        if ref is None:
            return ""
        caption = _clean_caption(ref.caption) if captions else ""
        suffix = f' "{caption}"' if caption else ""
        return f"![图 {ref.id} · {hms(ref.time)}]({ref.image}{suffix})"

    return shift_headings(IMG_RE.sub(replace, section.markdown), 1).strip()


def _section_html(
    section: WrittenSection,
    job_dir: Path,
    link: str | None,
    config: RenderConfig,
) -> str:
    body = _body_html(section, job_dir, link, config)
    label = KIND_LABELS.get(section.kind, section.kind)
    stamp = hms(section.start)
    if link:
        stamp = (
            f'<a href="{html.escape(_titled(link, section.start))}" '
            f'target="_blank" rel="noreferrer">{stamp}</a>'
        )
    meta = f"{stamp} – {hms(section.end)} · {html.escape(label)}"
    return f"""<section class="sec" id="s{section.index}">
<h2>{html.escape(section.title)}</h2>
<div class="sec-meta">{meta}</div>
{body}
</section>"""


def _body_html(
    section: WrittenSection,
    job_dir: Path,
    link: str | None,
    config: RenderConfig,
) -> str:
    return render_fragment(
        section.markdown,
        section.images,
        job_dir=job_dir,
        link=link,
        config=config,
        title=section.title,
    )


def render_fragment(
    markdown: str,
    refs: list[ImageRef],
    *,
    job_dir: Path,
    link: str | None,
    config: RenderConfig,
    title: str = "",
) -> str:
    """渲染一小段正文（含 `[[img:N]]` 占位符）。写正文和校对预览共用。"""
    # 正文里的 ## 属于小节，整体降一级，页面层级才不会乱
    body = _MD.render(shift_headings(markdown, 1))

    for ref in refs:
        figure = _figure_html(ref, title, job_dir, link, config)
        token = f"[[img:{ref.id}]]"
        body = body.replace(f"<p>{token}</p>", figure)
        body = body.replace(token, figure)

    # 越界编号和模型漏删的占位符一律清掉，别让它们出现在成品里
    return IMG_RE.sub("", body).strip()


def _figure_html(
    ref: ImageRef,
    title: str,
    job_dir: Path,
    link: str | None,
    config: RenderConfig,
) -> str:
    src = _image_src(ref.image, job_dir, config.embed_images)
    stamp = html.escape(hms(ref.time))
    if link:
        stamp = (
            f'<a href="{html.escape(_titled(link, ref.time))}" '
            f'target="_blank" rel="noreferrer">{stamp}</a>'
        )
    caption = _clean_caption(ref.caption) if config.image_captions else ""
    note = f'<span class="note">画面文字：{html.escape(caption)}</span>' if caption else ""
    alt = html.escape(f"{title} 画面 {hms(ref.time)}")
    return (
        f'<figure>\n<img src="{html.escape(src, quote=True)}" alt="{alt}" loading="lazy">\n'
        f'<figcaption><span class="ts">{stamp}</span>{note}</figcaption>\n</figure>'
    )


def _toc(document: Document) -> str:
    items = "\n".join(
        f'<li><a href="#s{section.index}"><span class="n">{index:02d}</span>'
        f'<span class="t">{html.escape(section.title)}</span>'
        f'<span class="time">{hms(section.start)}</span></a></li>'
        for index, section in enumerate(document.sections, 1)
    )
    return f'<nav class="toc">\n<div class="label">目录</div>\n<ol>\n{items}\n</ol>\n</nav>'


def _chips(
    document: Document,
    source: SourceInfo | None,
    critique: Critique | None,
    link: str | None,
) -> str:
    chips = [f'<span class="chip">时长 {hms(document.duration)}</span>']
    chips.append(f'<span class="chip">{len(document.sections)} 节</span>')
    if document.images:
        chips.append(f'<span class="chip">{document.images} 张画面</span>')
    if critique is not None:
        chips.append(f'<span class="chip">覆盖 {critique.coverage:.0%}</span>')
    if source is not None and source.uploader:
        chips.append(f'<span class="chip">{html.escape(source.uploader)}</span>')
    if link:
        chips.append(
            f'<span class="chip"><a href="{html.escape(link)}" target="_blank" '
            f'rel="noreferrer">看原视频 ↗</a></span>'
        )
    return "".join(chips)


def _footer(
    document: Document,
    critique: Critique | None,
    source: SourceInfo | None,
    page_title: str,
) -> str:
    parts = ["由 video2doc 根据视频转写与画面自动整理"]
    if document.model:
        parts.append(f"模型 {html.escape(document.model)}")
    parts.append(f"生成于 {html.escape(now_iso()[:16].replace('T', ' '))}")
    line = " · ".join(parts)
    notes = []
    if source is not None and source.title and source.title != page_title:
        notes.append(f"原视频：{source.title}")
    notes.append("画面文字来自 OCR 识别，代码里的符号可能有误，请对照原片核对。")
    if critique is not None and critique.issues:
        notes.append(f"质检有 {len(critique.issues)} 条提示，覆盖率 {critique.coverage:.0%}。")
    body = "".join(f"<p>{html.escape(note)}</p>" for note in notes)
    return f"<p>{line}</p>\n{body}"


# ------------------------------------------------------------------ 工具


def video_link(source: SourceInfo | None) -> str | None:
    if source is None or source.kind != "url":
        return None
    return source.webpage_url or source.url


def _titled(link: str, seconds: float) -> str:
    return f"{link}{'&' if '?' in link else '?'}t={int(seconds)}"


def _clean_caption(text: str, limit: int = 60) -> str:
    return " ".join((text or "").split())[:limit]


def _image_src(rel_path: str, job_dir: Path, embed: bool) -> str:
    if not embed:
        return rel_path
    data = _embed(job_dir / rel_path)
    return data or rel_path


def _embed(path: Path, width: int = _MAX_IMAGE_WIDTH) -> str:
    if not path.exists():
        return ""
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            if rgb.width > width:
                ratio = width / rgb.width
                rgb = rgb.resize((width, max(1, round(rgb.height * ratio))), Image.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, "WEBP", quality=_IMAGE_QUALITY, method=4)
    except OSError:
        return ""
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{payload}"
