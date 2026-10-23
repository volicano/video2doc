from __future__ import annotations

from types import SimpleNamespace

import pytest

from video2doc.config import WriteConfig
from video2doc.schema import (
    Section,
    Segmentation,
    Transcript,
    Utterance,
    VisualMoment,
)
from video2doc.writing.materials import assign_moments, build_material, caption_for
from video2doc.writing.write import sanitize_markdown, write_document, write_section


class FakeLLM:
    def __init__(self, writer, intro: str = "这是一段导语。", intro_title: str = "") -> None:
        self.writer = writer
        self.intro = intro
        self.intro_title = intro_title
        self.calls = 0
        self.images: list[int] = []
        self.prompts: list[str] = []
        self.config = SimpleNamespace(model="fake")

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        self.calls += 1
        self.prompts.append(user)
        if "导语" in system:
            payload = {"intro": self.intro}
            if self.intro_title:
                payload["title"] = self.intro_title
            return payload
        return self.writer(len(self.images))


class FlakyLLM:
    def __init__(self, tag: str, *, fail_at: int | None = None) -> None:
        self.tag = tag
        self.fail_at = fail_at
        self.calls = 0
        self.config = SimpleNamespace(model="fake")

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        self.calls += 1
        if self.fail_at is not None and self.calls == self.fail_at:
            raise RuntimeError("boom")
        if "导语" in system:
            return {"title": "检查点测试", "intro": "导语。"}
        return {"summary": "摘要", "markdown": f"{self.tag} section {self.calls}"}


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


def test_caption_filters_browser_noise() -> None:
    noisy = VisualMoment(
        index=1,
        image="frames/a.jpg",
        time=1.0,
        start=0.0,
        end=5.0,
        ocr_text="bilibili\ncalen\n123\n零基础入门",
    )
    assert caption_for(noisy) == "零基础入门"

    junk = VisualMoment(
        index=2,
        image="frames/b.jpg",
        time=1.0,
        start=0.0,
        end=5.0,
        ocr_text="bilibili\ntags\nRaw",
    )
    assert caption_for(junk) == ""


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


def test_build_material_keeps_code_layout() -> None:
    section = two_sections().sections[0]
    code_moment = moment(1, 10, 20)
    code_moment.ocr_text = (
        "const task = await useOrCreateTaskSpace('search');\nfunction run() {\n  return task;\n}"
    )

    material = build_material(section, make_transcript().utterances, [code_moment], max_images=1)

    prompt = material.prompt_images[0]
    assert "【代码画面】" in prompt
    assert "\n  return task;" in prompt
    assert "const task" in prompt


def test_code_frame_survives_image_limit() -> None:
    section = two_sections().sections[0]
    moments = [
        moment(1, 10, 20, score=0.9),
        moment(2, 30, 40, score=0.8),
        moment(3, 50, 60, score=0.7),
    ]
    moments[2].ocr_text = "const x = 1;\nfunction run() { return x; }"

    material = build_material(section, make_transcript().utterances, moments, max_images=2)

    assert len(material.prompt_images) == 2
    assert any("【代码画面】" in line for line in material.prompt_images)
    assert any("function run" in line for line in material.prompt_images)


def test_write_section_backfills_code_block() -> None:
    section = two_sections().sections[0]
    code_moment = moment(1, 10, 20)
    code_moment.ocr_text = (
        "ego-browser nodejs<<'EOF'\n"
        "const task = await useOrCreateTaskSpace('search');\n"
        "await openOrReuseTab('https://github.com/issues');\n"
        "EOF"
    )
    llm = FakeLLM(
        lambda call: {
            "summary": "",
            "markdown": "正文。\n\n> 代码为画面识别结果，符号可能有误，建议对照原片核对。",
        }
    )

    written = write_section(
        section,
        make_transcript().utterances,
        [code_moment],
        llm,
        WriteConfig(max_images_per_section=1),
        language="zh",
    )

    assert "```js" in written.markdown
    assert "const task" in written.markdown
    assert written.markdown.count("> 代码为画面识别结果") == 1


def test_write_section_includes_cleaned_code_in_prompt() -> None:
    section = two_sections().sections[0]
    code_moment = moment(1, 10, 20)
    code_moment.ocr_text = (
        "ego-browser nodejs<<'EOF'\n"
        "const task = await useOrCreateTaskSpace('search');\n"
        "await openOrReuseTab('https://github.com/issues');\n"
        "EOF"
    )
    llm = FakeLLM(lambda call: {"summary": "", "markdown": "正文。"})

    write_section(
        section,
        make_transcript().utterances,
        [code_moment],
        llm,
        WriteConfig(max_images_per_section=1),
        language="zh",
    )

    assert "识别到的代码片段" in llm.prompts[0]
    assert "const task = await useOrCreateTaskSpace" in llm.prompts[0]


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


def test_write_checkpoint_survives_failure(tmp_path) -> None:
    checkpoint = tmp_path / "cache" / "write_sections.json"
    kwargs = {
        "segmentation": two_sections(),
        "transcript": make_transcript(),
        "moments": [moment(1, 0, 60), moment(2, 120, 180)],
        "config": WriteConfig(),
        "title": "测试",
        "language": "zh",
        "checkpoint": checkpoint,
        "cache_version": "test",
    }

    with pytest.raises(RuntimeError, match="boom"):
        write_document(provider=FlakyLLM("first", fail_at=2), **kwargs)

    assert checkpoint.exists()

    provider = FlakyLLM("second")
    document = write_document(provider=provider, **kwargs)

    # 第一节复用检查点，只重新写第二节和导语
    assert provider.calls == 2
    assert "first section 1" in document.sections[0].markdown
    assert "second section 1" in document.sections[1].markdown
    assert not checkpoint.exists()


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


def test_intro_can_replace_the_video_title() -> None:
    llm = FakeLLM(
        lambda call: {"summary": "", "markdown": "正文内容 [[img:1]]"},
        intro_title="Obsidian 8 个插件：从 0 到 1 的笔记系统",
    )
    document = write_document(
        two_sections(),
        make_transcript(),
        [],
        llm,
        WriteConfig(),
        title="【Obsidian教程】入门到精通，AI学习办公必备的8个插件，不写代码就能用",
        language="zh",
    )
    assert document.title == "Obsidian 8 个插件：从 0 到 1 的笔记系统"


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
