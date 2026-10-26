from __future__ import annotations

from typing import Any

import pytest

import video2doc.stages.correct as correct_module
from video2doc.config import Config
from video2doc.schema import Transcript, Utterance
from video2doc.stages.correct import CorrectStage, _apply, _batches


def _transcript(texts: list[str]) -> Transcript:
    return Transcript(
        source="asr",
        provider="faster-whisper",
        model="tiny",
        language="zh",
        duration=float(len(texts) * 5),
        utterances=[
            Utterance(start=float(i * 5), end=float(i * 5 + 4), text=text)
            for i, text in enumerate(texts)
        ],
    )


class FakeProvider:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {"items": []}
        self.calls = 0

    def complete_json(self, system: str, user: str, *, progress=None) -> dict[str, Any]:
        self.calls += 1
        return self.response


class StubContext:
    """只实现 CorrectStage 真正用到的那几个成员。"""

    def __init__(self, transcript: Transcript, config: Config) -> None:
        self._transcript = transcript
        self.config = config

    def load(self, name: str, model: type) -> Any:
        return self._transcript

    def detail(self, message: str) -> None:
        pass

    def progress(self, message: str) -> None:
        pass


def test_apply_validates_indices_and_lengths() -> None:
    batch = [
        Utterance(start=0, end=1, text="什么是弊包"),
        Utterance(start=1, end=2, text="它是寒树和此法环境的组合"),
    ]
    changed = _apply(
        batch,
        [
            {"i": 0, "t": "什么是闭包"},
            {"i": 1, "t": "它是函数和词法环境的组合"},
            {"i": 99, "t": "越界"},  # 丢弃
            {"i": "x", "t": "非法"},  # 丢弃
            {"i": 0, "t": "完全重写的一句话，长度也明显不对，应该被丢弃掉"},  # 长度守卫
        ],
    )
    assert changed == 2
    assert batch[0].text == "什么是闭包"
    assert batch[1].text == "它是函数和词法环境的组合"
    # 时间戳必须原样保留
    assert (batch[0].start, batch[0].end) == (0, 1)


def test_stage_counts_and_keeps_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProvider(
        {
            "items": [
                {"i": 0, "t": "什么是闭包?"},
                {"i": 2, "t": "因为闭包引用的变量不会被垃圾回收。"},
            ]
        }
    )
    monkeypatch.setattr(correct_module, "create_llm_from_config", lambda cfg: fake)

    transcript = _transcript(
        ["什么是弊包?", "它为什么会导致内存泄漏?", "因为弊包引用的变量不会被拉机回收。"]
    )
    config = Config()
    config.asr.correct_with_llm = True
    config.asr.glossary = ["闭包", "垃圾回收"]

    result = CorrectStage().run(StubContext(transcript, config))

    assert isinstance(result, Transcript)
    assert result.corrections == 2
    assert result.utterances[0].text == "什么是闭包?"
    assert result.utterances[2].text == "因为闭包引用的变量不会被垃圾回收。"
    assert result.utterances[0].start == 0
    assert result.utterances[2].start == 10


def test_prompt_protects_proper_nouns() -> None:
    assert "专有名词" in correct_module.SYSTEM_PROMPT
    assert "不要互相替换" in correct_module.SYSTEM_PROMPT


def test_batching_splits_by_count() -> None:
    utterances = [Utterance(start=i, end=i + 1, text="短句") for i in range(130)]
    batches = list(_batches(utterances))
    assert len(batches) == 3
    assert sum(len(batch) for batch in batches) == 130


def test_batching_splits_by_chars() -> None:
    utterances = [Utterance(start=i, end=i + 1, text="字" * 2000) for i in range(4)]
    batches = list(_batches(utterances, max_chars=3000))
    assert all(len(batch) <= 1 for batch in batches)


def test_skip_when_disabled() -> None:
    reason = CorrectStage().skip(StubContext(_transcript(["a"]), Config()))
    assert reason is not None
    assert "correct_with_llm" in reason
