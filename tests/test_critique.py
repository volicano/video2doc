from __future__ import annotations

from types import SimpleNamespace

from video2doc.config import WriteConfig
from video2doc.schema import (
    Document,
    ImageRef,
    Section,
    Segmentation,
    Transcript,
    Utterance,
    VisualMoment,
    WrittenSection,
)
from video2doc.writing.critique import check, critique_document


class FakeLLM:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.calls = 0
        self.config = SimpleNamespace(model="fake")

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        self.calls += 1
        return {"summary": "重写后的摘要", "markdown": self.markdown}


def make_transcript(count: int = 20, step: float = 10.0) -> Transcript:
    return Transcript(
        source="asr",
        provider="fake",
        duration=count * step,
        utterances=[
            Utterance(
                start=index * step,
                end=index * step + step,
                text=f"这是第 {index} 句的讲解内容，用来测试覆盖率和长度判断。",
            )
            for index in range(count)
        ],
    )


def make_segmentation() -> Segmentation:
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
            ),
            Section(
                index=2,
                title="第二节",
                start=100.0,
                end=200.0,
                start_index=10,
                end_index=20,
            ),
        ],
    )


def make_document(
    markdown: str = "讲解内容。" * 60,
    images: list[ImageRef] | None = None,
) -> Document:
    section = WrittenSection(
        index=1,
        title="第一节",
        start=0.0,
        end=100.0,
        markdown=markdown,
        images=images or [],
        chars=len(markdown),
    )
    second = WrittenSection(
        index=2,
        title="第二节",
        start=100.0,
        end=200.0,
        markdown="第二节正文。" * 40,
        chars=200,
    )
    return Document(title="测试", duration=200.0, sections=[section, second])


def test_healthy_document_passes() -> None:
    result = check(make_document(), make_segmentation(), make_transcript(), [])
    assert result.passed
    assert result.coverage == 1.0
    assert not [issue for issue in result.issues if issue.severity == "high"]


def test_empty_section_is_high_severity() -> None:
    document = make_document(markdown="略")
    result = check(document, make_segmentation(), make_transcript(), [])
    assert not result.passed
    assert any(issue.code == "empty" and issue.severity == "high" for issue in result.issues)


def test_images_present_but_not_referenced() -> None:
    ref = ImageRef(id=1, time=10.0, image="frames/f01.jpg", kind="slide")
    document = make_document(markdown="正文里没有放图。" * 30, images=[ref])
    moment = VisualMoment(index=1, image="frames/f01.jpg", time=10.0, start=0.0, end=40.0)
    result = check(document, make_segmentation(), make_transcript(), [moment])
    assert any(issue.code == "no_image" for issue in result.issues)


def test_code_on_screen_requires_fence() -> None:
    ref = ImageRef(id=1, time=10.0, image="frames/f01.jpg", kind="code")
    markdown = "看这段代码。[[img:1]]\n\n" + "说明文字。" * 40
    document = make_document(markdown=markdown, images=[ref])
    moment = VisualMoment(
        index=1,
        image="frames/f01.jpg",
        time=10.0,
        start=0.0,
        end=40.0,
        kind="code",
        ocr_text="function outer() { return () => {}; }",
    )
    result = check(document, make_segmentation(), make_transcript(), [moment])
    assert any(issue.code == "code_missing" for issue in result.issues)


def test_review_rewrites_only_flagged_sections() -> None:
    document = make_document(markdown="略")
    llm = FakeLLM("## 小节\n" + "补写后的正文内容。" * 20)
    result = critique_document(
        document,
        make_segmentation(),
        make_transcript(),
        [],
        provider=llm,
        config=WriteConfig(review=True, max_rewrites=3),
        language="zh",
    )
    assert llm.calls == 1
    assert result.rewrites == 1
    assert result.passed
    assert document.sections[0].rewritten
    assert document.sections[0].chars > 20


class FailingLLM:
    config = SimpleNamespace(model="fake")

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        from video2doc.errors import StageError

        self.calls += 1
        raise StageError("接口挂了")


def test_review_failure_keeps_original() -> None:
    document = make_document(markdown="略")
    llm = FailingLLM()
    result = critique_document(
        document,
        make_segmentation(),
        make_transcript(),
        [],
        provider=llm,
        config=WriteConfig(review=True),
        language="zh",
    )
    assert llm.calls == 1
    assert result.rewrites == 0
    assert document.sections[0].markdown == "略"
    assert not result.passed


def test_review_can_be_disabled() -> None:
    document = make_document(markdown="略")
    llm = FakeLLM("不会用到的重写")
    result = critique_document(
        document,
        make_segmentation(),
        make_transcript(),
        [],
        provider=llm,
        config=WriteConfig(review=False),
        language="zh",
    )
    assert llm.calls == 0
    assert result.rewrites == 0
    assert not result.passed
