from __future__ import annotations

from types import SimpleNamespace

import pytest

from video2doc.config import WriteConfig
from video2doc.errors import StageError
from video2doc.schema import Section, Segmentation, Transcript, Utterance, VisualMoment
from video2doc.writing.rewrite import rewrite_section
from video2doc.writing.write import write_document


class FakeLLM:
    def __init__(self, writer, *, title: str = "") -> None:
        self.writer = writer
        self.title = title
        self.prompts: list[str] = []
        self.config = SimpleNamespace(model="fake")

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        self.prompts.append(user)
        if "导语" in system:
            return {"intro": "导语"}
        payload = self.writer(len(self.prompts))
        if self.title:
            payload["title"] = self.title
        return payload


def transcript() -> Transcript:
    return Transcript(
        source="asr",
        provider="fake",
        duration=200.0,
        utterances=[
            Utterance(start=index * 10.0, end=index * 10.0 + 10.0, text=f"第 {index} 句。")
            for index in range(20)
        ],
    )


def segmentation() -> Segmentation:
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


def moments() -> list[VisualMoment]:
    return [
        VisualMoment(
            index=1,
            image="frames/f01.jpg",
            time=10.0,
            start=0.0,
            end=40.0,
            kind="slide",
            score=0.5,
            ocr_text="第一页",
        ),
        VisualMoment(
            index=2,
            image="frames/f02.jpg",
            time=120.0,
            start=110.0,
            end=150.0,
            kind="slide",
            score=0.6,
            ocr_text="第二页",
        ),
    ]


def build_document():
    llm = FakeLLM(lambda call: {"summary": "摘要", "markdown": "原文正文。\n\n[[img:1]]"})
    return write_document(
        segmentation(),
        transcript(),
        moments(),
        llm,
        WriteConfig(),
        title="原始标题",
        language="zh",
    )


def test_rewrite_replaces_only_the_target_section() -> None:
    document = build_document()
    first_before = document.sections[0].model_dump()

    llm = FakeLLM(lambda call: {"summary": "新摘要", "markdown": "重写后的第二节。"})
    updated, written = rewrite_section(
        document,
        segmentation(),
        transcript(),
        moments(),
        llm,
        WriteConfig(),
        section_index=2,
        language="zh",
    )

    assert updated.sections[0].model_dump() == first_before
    assert updated.sections[1].markdown.startswith("重写后的第二节。")
    assert updated.sections[1].summary == "新摘要"
    assert written.rewritten is True
    assert updated.sections[1].rewritten is True
    assert updated.chars == sum(section.chars for section in updated.sections)
    assert updated.images == sum(len(section.images) for section in updated.sections)
    # 只调了一次模型（没有重写导语）
    assert len(llm.prompts) == 1


def test_hint_and_generated_title_are_used() -> None:
    document = build_document()
    llm = FakeLLM(
        lambda call: {"summary": "摘要", "markdown": "重写后的第二节。"},
        title="任务接管与终止",
    )

    updated, written = rewrite_section(
        document,
        segmentation(),
        transcript(),
        moments(),
        llm,
        WriteConfig(),
        section_index=2,
        language="zh",
        hint="这节讲的是任务接管，不要写成 Profile 隔离",
    )

    assert "这节讲的是任务接管" in llm.prompts[0]
    assert "title 字段" in llm.prompts[0]
    assert written.title == "任务接管与终止"
    assert updated.sections[1].title == "任务接管与终止"


def test_unknown_section_lists_available_ones() -> None:
    document = build_document()

    with pytest.raises(StageError) as info:
        rewrite_section(
            document,
            segmentation(),
            transcript(),
            moments(),
            FakeLLM(lambda call: {}),
            WriteConfig(),
            section_index=5,
            language="zh",
        )

    assert "没有第 5 节" in info.value.message
    assert "1, 2" in (info.value.hint or "")
