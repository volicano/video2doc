from __future__ import annotations

from pathlib import Path

import pytest

from video2doc.errors import StageError
from video2doc.overrides import (
    OVERRIDES_NAME,
    Overrides,
    SectionOverride,
    apply_overrides,
    load_overrides,
    overrides_digest,
    save_overrides,
)
from video2doc.schema import Document, ImageRef, WrittenSection
from video2doc.writing.materials import count_chars


def ref(index: int, image: str) -> ImageRef:
    return ImageRef(id=index, time=float(index * 10), image=image, kind="slide")


def sample() -> Document:
    first = WrittenSection(
        index=1,
        title="第一节",
        start=0.0,
        end=100.0,
        markdown="第一节正文。\n\n[[img:1]]",
        images=[ref(1, "frames/a.jpg")],
        chars=6,
    )
    second = WrittenSection(
        index=2,
        title="第二节",
        start=100.0,
        end=200.0,
        markdown="第二节正文。\n\n[[img:1]]",
        images=[ref(1, "frames/b.jpg")],
        chars=6,
    )
    return Document(
        title="模型标题",
        intro="模型导语",
        duration=200.0,
        sections=[first, second],
        chars=12,
        images=2,
    )


def test_empty_overrides_leave_document_untouched() -> None:
    overrides = Overrides()
    assert overrides.is_empty()

    updated, warnings = apply_overrides(sample(), overrides)

    assert updated == sample()
    assert warnings == []


def test_title_and_intro_override() -> None:
    overrides = Overrides(title="人工标题", intro="人工导语")
    updated, warnings = apply_overrides(sample(), overrides)

    assert updated.title == "人工标题"
    assert updated.intro == "人工导语"
    assert warnings == []


def test_section_title_and_markdown_override() -> None:
    overrides = Overrides(
        sections={2: SectionOverride(title="新的第二节", markdown="完全重写的正文。")}
    )
    updated, warnings = apply_overrides(sample(), overrides)

    assert updated.sections[1].title == "新的第二节"
    assert updated.sections[1].markdown == "完全重写的正文。"
    assert updated.sections[1].chars == count_chars("完全重写的正文。")
    assert updated.sections[0].title == "第一节"
    assert updated.chars == sum(section.chars for section in updated.sections)
    assert warnings == []


def test_images_can_be_reordered_and_matched_by_basename() -> None:
    overrides = Overrides(
        sections={1: SectionOverride(images=["b.jpg", "frames/a.jpg"])}
    )
    updated, warnings = apply_overrides(sample(), overrides)

    refs = updated.sections[0].images
    assert [item.image for item in refs] == ["frames/b.jpg", "frames/a.jpg"]
    assert [item.id for item in refs] == [1, 2]
    # b 挪到第 1 节后，第 2 节就不再重复出现它
    assert updated.sections[1].images == []
    assert updated.images == 2
    # 第 2 节正文里还留着 [[img:1]]，这一条要提示出来
    assert any("第 2 节" in warning and "[[img:1]]" in warning for warning in warnings)


def test_unknown_image_is_reported_and_dropped() -> None:
    overrides = Overrides(sections={1: SectionOverride(images=["frames/nope.jpg"])})
    updated, warnings = apply_overrides(sample(), overrides)

    assert updated.sections[0].images == []
    assert updated.images == 1
    assert any("nope.jpg" in warning for warning in warnings)


def test_out_of_range_image_token_warns() -> None:
    overrides = Overrides(
        sections={1: SectionOverride(markdown="正文\n\n[[img:3]]", images=["frames/a.jpg"])}
    )
    updated, warnings = apply_overrides(sample(), overrides)

    assert "[[img:3]]" in updated.sections[0].markdown
    assert any("[[img:3]]" in warning for warning in warnings)


def test_unknown_section_warns() -> None:
    overrides = Overrides(sections={9: SectionOverride(title="不存在")})
    _, warnings = apply_overrides(sample(), overrides)

    assert any("第 9 节" in warning for warning in warnings)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    overrides = Overrides(
        title="标题",
        sections={1: SectionOverride(markdown="正文", images=["frames/a.jpg"])},
    )
    path = save_overrides(tmp_path, overrides)

    assert path.name == OVERRIDES_NAME
    loaded = load_overrides(tmp_path)
    assert loaded is not None
    assert loaded.title == "标题"
    assert loaded.sections[1].images == ["frames/a.jpg"]


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_overrides(tmp_path) is None
    assert overrides_digest(tmp_path) == ""


def test_corrupt_file_raises_with_hint(tmp_path: Path) -> None:
    (tmp_path / OVERRIDES_NAME).write_text("{ 不是 json", encoding="utf-8")

    with pytest.raises(StageError) as info:
        load_overrides(tmp_path)

    assert OVERRIDES_NAME in info.value.message
    assert info.value.hint


def test_digest_changes_with_content(tmp_path: Path) -> None:
    save_overrides(tmp_path, Overrides(title="一"))
    first = overrides_digest(tmp_path)

    save_overrides(tmp_path, Overrides(title="二"))
    second = overrides_digest(tmp_path)

    assert first and second
    assert first != second
