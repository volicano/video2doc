from __future__ import annotations

from types import SimpleNamespace

from video2doc.config import WriteConfig
from video2doc.schema import (
    Section,
    Segmentation,
    Transcript,
    Utterance,
    VisualMoment,
)
from video2doc.writing.materials import assign_moments, build_material
from video2doc.writing.write import sanitize_markdown, write_document


class FakeLLM:
    def __init__(self, writer, intro: str = "这是一段导语。") -> None:
        self.writer = writer
        self.intro = intro
        self.calls = 0
        self.images: list[int] = []
        self.config = SimpleNamespace(model="fake")

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        self.calls += 1
        if "导语" in system:
            return {"intro": self.intro}
        return self.writer(len(self.images))


def make_transcript(count: int = 20, step: float = 10.0) -> Transcript:
    return Transcript(
        source="asr",
        provider="fake",
        duration=count * step,
        utterances=[
            Utterance(
                start=index * step,
                end=index * step + step,
                text=f"第 {index} 句的讲解内容。",
            )
            for index in range(count)
        ],
    )


def moment(index: int, start: float, end: float, score: float = 0.5) -> VisualMoment:
    return VisualMoment(
        index=index,
        image=f"frames/f{index:02d}.jpg",
        time=start,
        start=start,
        end=end,
        kind="slide",
        score=score,
        ocr_text=f"第 {index} 页标题\n一些说明文字",
    )


def two_sections() -> Segmentation:
    return Segmentation(
        duration=200.0,
        sections=[
            Section(
                index=1,
                title="第一节",
                start=0.0,
                end=100.0,
                start_index=0,
                end_index=10,
                utterances=10,
            ),
            Section(
                index=2,
                title="第二节",
                start=100.0,
                end=200.0,
                start_index=10,
                end_index=20,
                utterances=10,
            ),
        ],
    )


# ------------------------------------------------------------------ 材料


def test_assign_moments_gives_each_image_to_max_overlap() -> None:
    sections = two_sections().sections
    moments = [moment(1, 0, 50), moment(2, 90, 110), moment(3, 120, 160)]
    buckets = assign_moments(sections, moments)
    # 90–110 横跨两节，重叠都是 10s，归给先遇到的那节（索引小）
    assert [item.index for item in buckets[1]] == [1, 2]
    assert [item.index for item in buckets[2]] == [3]


def test_build_material_caps_and_sorts_images() -> None:
    section = two_sections().sections[0]
    moments = [
        moment(1, 10, 20, score=0.1),
        moment(2, 40, 50, score=0.9),
        moment(3, 70, 80, score=0.5),
        moment(4, 90, 95, score=0.8),
    ]
    material = build_material(section, make_transcript().utterances, moments, max_images=3)
    assert [ref.id for ref in material.refs] == [1, 2, 3]
    assert [ref.image for ref in material.refs] == [
        "frames/f02.jpg",
        "frames/f03.jpg",
        "frames/f04.jpg",
    ]
    assert material.prompt_images[0].startswith("[1] 00:40")
    assert "第 2 页标题" in material.prompt_images[0]


# -------------------------------------------------------------- 清洗 Markdown


def test_sanitize_removes_out_of_range_tokens() -> None:
    text = sanitize_markdown("正文一段。\n\n[[img:9]]", image_count=2)
    assert "[[img:9]]" not in text
    # 一张合法图都没用上时自动补第一张
    assert text.endswith("[[img:1]]")


def test_sanitize_keeps_valid_tokens() -> None:
    text = sanitize_markdown("开头\n\n[[img:2]]\n\n结尾", image_count=2)
    assert "[[img:2]]" in text
    assert text.count("[[img:") == 1


def test_sanitize_puts_inline_token_on_its_own_line() -> None:
    text = sanitize_markdown("前面[[img:1]]后面", image_count=1)
    assert "\n\n[[img:1]]\n\n" in text
    assert not text.startswith("[[img:")


def test_sanitize_demotes_h1_but_not_code_comments() -> None:
    raw = "# 大标题\n\n```python\n# 这是注释\nprint(1)\n```\n"
    text = sanitize_markdown(raw, image_count=0)
    assert text.startswith("## 大标题")
    assert "# 这是注释" in text


def test_sanitize_strips_markdown_wrapper() -> None:
    raw = "```markdown\n## 小节\n正文\n```"
    assert sanitize_markdown(raw, image_count=0) == "## 小节\n正文"


# ------------------------------------------------------------------ 整节写作


def test_write_document_builds_sections_and_intro() -> None:
    transcript = make_transcript()
    moments = [moment(1, 0, 60), moment(2, 120, 180)]

    def writer(call: int) -> dict:
        return {"summary": "本节摘要", "markdown": "## 小节\n讲解内容。\n\n[[img:1]]"}

    llm = FakeLLM(writer)
    document = write_document(
        two_sections(),
        transcript,
        moments,
        llm,
        WriteConfig(max_images_per_section=4),
        title="测试教程",
        language="zh",
    )

    assert document.title == "测试教程"
    assert document.intro == "这是一段导语。"
    assert len(document.sections) == 2
    assert document.images == 2
    assert document.sections[0].images[0].image == "frames/f01.jpg"
    assert all(section.chars > 0 for section in document.sections)
    assert document.sections[0].markdown.endswith("[[img:1]]")


def test_duplicate_leading_title_is_removed() -> None:
    llm = FakeLLM(lambda call: {"summary": "", "markdown": "## 第一节\n正文内容。"})
    document = write_document(
        two_sections(),
        make_transcript(),
        [],
        llm,
        WriteConfig(),
        title="t",
        language="zh",
    )
    assert document.sections[0].markdown.startswith("正文内容")


def test_write_document_falls_back_when_model_returns_nothing() -> None:
    llm = FakeLLM(lambda call: {"summary": "", "markdown": ""})
    document = write_document(
        two_sections(),
        make_transcript(),
        [moment(1, 0, 60)],
        llm,
        WriteConfig(),
        title="t",
        language="zh",
    )
    assert all(section.markdown.strip() for section in document.sections)
    assert "第 0 句" in document.sections[0].markdown
    # 空响应会重试一次，再退化成原始转写
    assert llm.calls >= 3
