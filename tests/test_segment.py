from __future__ import annotations

from types import SimpleNamespace

from video2doc.config import WriteConfig
from video2doc.schema import Transcript, Utterance
from video2doc.writing.segment import _clean_title, plan_chunks, segment_transcript


class FakeLLM:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.config = SimpleNamespace(model="fake")

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        self.calls += 1
        return self.payloads.pop(0) if self.payloads else {"sections": []}


def make_transcript(count: int = 60, step: float = 10.0) -> Transcript:
    utterances = [
        Utterance(
            start=index * step,
            end=index * step + step - 1,
            text=f"第 {index} 句，讲一点内容。",
        )
        for index in range(count)
    ]
    return Transcript(
        source="asr",
        provider="fake",
        duration=count * step,
        utterances=utterances,
    )


def config(**overrides) -> WriteConfig:
    base = {
        "chunk_utterances": 1000,
        "chunk_overlap": 0,
        "min_section_sec": 60.0,
        "max_section_sec": 600.0,
        "max_sections": 12,
    }
    base.update(overrides)
    return WriteConfig(**base)


def test_plan_chunks_overlaps_and_covers_tail() -> None:
    chunks = plan_chunks(100, 40, 10)
    assert chunks[0] == (0, 40)
    assert all(end - start <= 40 for start, end in chunks)
    assert chunks[-1][1] == 100
    assert all(later[0] < earlier[1] for earlier, later in zip(chunks, chunks[1:], strict=False))


def test_plan_chunks_short_transcript() -> None:
    assert plan_chunks(12, 40, 10) == [(0, 12)]
    assert plan_chunks(0, 40, 10) == []


def test_plan_chunks_caps_absurd_overlap() -> None:
    # 即使直接调用（绕过配置校验），也不能把步长压到 1 而产生几十个窗口。
    assert len(plan_chunks(100, 40, 40)) == 4
    assert len(plan_chunks(100, 40, 100)) == 4


def test_segments_by_utterance_index_and_derives_time() -> None:
    transcript = make_transcript(60)
    llm = FakeLLM(
        [
            {
                "sections": [
                    {"at": 0, "title": "开场与目标", "kind": "setup"},
                    {"at": 20, "title": "闭包的形成过程", "kind": "concept"},
                    {"at": 40, "title": "动手演示内存泄漏", "kind": "example"},
                ]
            }
        ]
    )

    result = segment_transcript(transcript, llm, config(), title="测试视频", language="zh")

    assert [section.title for section in result.sections] == [
        "开场与目标",
        "闭包的形成过程",
        "动手演示内存泄漏",
    ]
    assert [section.start for section in result.sections] == [0.0, 200.0, 400.0]
    assert result.sections[-1].end == 600.0
    assert result.sections[0].kind == "setup"
    # 边界来自句子编号，模型没有机会编时间戳
    assert result.sections[1].start_index == 20


def test_too_close_boundary_is_dropped() -> None:
    transcript = make_transcript(60)
    llm = FakeLLM(
        [
            {
                "sections": [
                    {"at": 0, "title": "开场", "kind": "concept"},
                    {"at": 3, "title": "太快了", "kind": "concept"},
                ]
            }
        ]
    )
    result = segment_transcript(
        transcript, llm, config(min_section_sec=120), title="t", language="zh"
    )
    assert len(result.sections) == 1
    assert result.sections[0].title == "开场"


def test_long_section_is_split_at_pauses() -> None:
    transcript = make_transcript(60)
    llm = FakeLLM([{"sections": [{"at": 0, "title": "一镜到底", "kind": "concept"}]}])
    result = segment_transcript(
        transcript, llm, config(max_section_sec=200), title="t", language="zh"
    )
    assert len(result.sections) == 4
    assert all(section.end - section.start <= 200 for section in result.sections)
    assert result.sections[1].title.endswith("（续）")


def test_too_many_sections_are_merged() -> None:
    transcript = make_transcript(60)
    llm = FakeLLM(
        [
            {
                "sections": [
                    {"at": 0, "title": "A", "kind": "concept"},
                    {"at": 20, "title": "B", "kind": "concept"},
                    {"at": 40, "title": "C", "kind": "concept"},
                ]
            }
        ]
    )
    result = segment_transcript(transcript, llm, config(max_sections=2), title="t", language="zh")
    assert len(result.sections) == 2
    assert result.sections[-1].end == 600.0


def test_invalid_items_are_ignored() -> None:
    transcript = make_transcript(60)
    llm = FakeLLM(
        [
            {
                "sections": [
                    {"at": "abc", "title": "坏索引"},
                    {"at": 999, "title": "越界"},
                    {"at": 20, "title": ""},
                    {"at": 40, "title": "  有效标题  ", "kind": "unknown-kind"},
                ]
            }
        ]
    )
    result = segment_transcript(transcript, llm, config(), title="t", language="zh")
    assert len(result.sections) == 2
    assert result.sections[-1].title == "有效标题"
    assert result.sections[-1].kind == "concept"
    assert result.sections[-1].start == 400.0


def test_empty_response_falls_back_to_one_section() -> None:
    transcript = make_transcript(9, step=5.0)
    llm = FakeLLM([{"sections": []}])
    result = segment_transcript(transcript, llm, config(), title="t", language="zh")
    assert len(result.sections) == 1
    assert result.sections[0].start_index == 0
    assert result.sections[0].end_index == 9
    assert result.sections[0].title.startswith("第 0 句")


def test_long_title_is_cut_at_separator() -> None:
    title = "Settings Search、Recent Files 与 Calendar、以及每日笔记的设置"
    assert _clean_title(title) == "Settings Search、Recent Files 与 Calendar"
    assert _clean_title("短标题") == "短标题"
    # 没有合适分隔符时才硬截断
    assert len(_clean_title("x" * 80)) == 40


def test_multiple_chunks_keep_global_indices() -> None:
    transcript = make_transcript(100)
    llm = FakeLLM(
        [
            {"sections": [{"at": 0, "title": "第一段", "kind": "concept"}]},
            {"sections": [{"at": 80, "title": "第二段", "kind": "summary"}]},
        ]
    )
    result = segment_transcript(
        transcript,
        llm,
        config(chunk_utterances=60, chunk_overlap=10, max_section_sec=1000),
        title="t",
        language="zh",
    )
    assert llm.calls == 2
    assert [section.start for section in result.sections] == [0.0, 800.0]
    assert result.chunks == 2
