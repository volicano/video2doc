from __future__ import annotations

from pathlib import Path

from PIL import Image

from video2doc.config import RenderConfig
from video2doc.render.html import build_html, build_markdown
from video2doc.schema import (
    Critique,
    Document,
    ImageRef,
    SourceInfo,
    WrittenSection,
)


def make_image(path: Path, size: tuple[int, int] = (320, 180)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (24, 48, 96)).save(path, "JPEG")


def make_document() -> Document:
    return Document(
        title="闭包与内存泄漏",
        intro="这份教程讲清楚闭包为什么会延长变量的生命周期。",
        model="fake-model",
        language="zh",
        duration=600.0,
        chars=400,
        images=1,
        sections=[
            WrittenSection(
                index=1,
                title="闭包是什么",
                kind="concept",
                start=0.0,
                end=300.0,
                markdown=(
                    "## 从一个例子开始\n\n"
                    "闭包让内层函数记住了外层变量。<script>alert(1)</script>\n\n"
                    "[[img:1]]\n\n"
                    "```js\n// 注释不要被当成标题\nconst add = (a) => a + 1;\n```"
                ),
                images=[
                    ImageRef(
                        id=1,
                        time=12.0,
                        image="frames/f01.jpg",
                        kind="code",
                        caption="闭包示例",
                    )
                ],
                chars=120,
            ),
            WrittenSection(
                index=2,
                title="什么时候会泄漏",
                kind="example",
                start=300.0,
                end=600.0,
                markdown="定时器忘了清理，闭包就一直引用着变量。",
                chars=80,
            ),
        ],
    )


def source() -> SourceInfo:
    return SourceInfo(
        kind="url",
        path="x.mp4",
        title="闭包与内存泄漏",
        duration=600.0,
        uploader="某 UP 主",
        url="https://www.bilibili.com/video/BV1xx",
        webpage_url="https://www.bilibili.com/video/BV1xx?p=1",
    )


def test_build_html_embeds_images_and_replaces_tokens(tmp_path: Path) -> None:
    make_image(tmp_path / "frames" / "f01.jpg")
    html = build_html(
        make_document(),
        source(),
        Critique(coverage=1.0, images_used=1, images_available=1),
        job_dir=tmp_path,
        config=RenderConfig(embed_images=True),
    )
    assert "<title>闭包与内存泄漏</title>" in html
    assert '<h2>闭包是什么</h2>' in html
    assert "<figure>" in html
    assert "data:image/webp;base64," in html
    # 默认不显示 OCR 图注（录屏类视频噪声大）
    assert '<span class="note">' not in html
    assert "[[img:" not in html
    assert 'id="s1"' in html
    # 正文标题降一级，代码块里的注释不受影响
    assert "<h3>从一个例子开始</h3>" in html
    assert "// 注释不要被当成标题" in html
    # 模型给的原始 HTML 必须被转义
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # 时间戳链接带上 ?t=
    assert "https://www.bilibili.com/video/BV1xx?p=1&amp;t=0" in html


def test_image_captions_can_be_enabled(tmp_path: Path) -> None:
    make_image(tmp_path / "frames" / "f01.jpg")
    html = build_html(
        make_document(),
        source(),
        None,
        job_dir=tmp_path,
        config=RenderConfig(image_captions=True),
    )
    assert "画面文字：闭包示例" in html


def test_build_html_can_skip_toc_and_embedding(tmp_path: Path) -> None:
    make_image(tmp_path / "frames" / "f01.jpg")
    html = build_html(
        make_document(),
        source(),
        None,
        job_dir=tmp_path,
        config=RenderConfig(embed_images=False, show_toc=False),
    )
    assert '<nav class="toc">' not in html
    assert 'src="frames/f01.jpg"' in html


def test_build_markdown_keeps_relative_images(tmp_path: Path) -> None:
    text = build_markdown(make_document(), source())
    assert text.startswith("# 闭包与内存泄漏")
    assert "## 目录" in text
    assert "1. 闭包是什么 · 00:00" in text
    assert "![图 1 · 00:12](frames/f01.jpg)" in text
    # 正文小节比章标题低一级
    assert "### 从一个例子开始" in text
    # Markdown 导出里只留正文，不带模型漏下的占位符
    assert "[[img:" not in text
    assert "```js" in text


def test_build_markdown_can_include_captions() -> None:
    text = build_markdown(make_document(), source(), captions=True)
    assert '![图 1 · 00:12](frames/f01.jpg "闭包示例")' in text
