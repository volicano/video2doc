from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from video2doc.config import ASRConfig
from video2doc.providers.asr import faster_whisper as fw


class FakeSegment:
    start = 0.0
    end = 1.0
    text = " hello "
    avg_logprob = -0.1


class FakeInfo:
    duration = 1.0


class FakeModel:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def transcribe(self, audio, **kwargs):
        CALLS["normal"].append(kwargs)
        return iter([FakeSegment()]), FakeInfo()


class FakeBatch:
    def __init__(self, model) -> None:
        pass

    def transcribe(self, audio, **kwargs):
        CALLS["batch"].append(kwargs)
        return iter([FakeSegment()]), FakeInfo()


CALLS = {"normal": [], "batch": []}


def _provider(batch_size: int) -> fw.FasterWhisperProvider:
    return fw.FasterWhisperProvider(
        ASRConfig(
            provider="faster-whisper",
            device="cpu",
            compute_type="int8",
            batch_size=batch_size,
        )
    )


def test_batch_size_uses_batched_pipeline(monkeypatch) -> None:
    CALLS["normal"].clear()
    CALLS["batch"].clear()
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeModel, BatchedInferencePipeline=FakeBatch),
    )
    monkeypatch.setattr(fw, "_audio_input", lambda path: "audio")

    utterances = _provider(8).transcribe(Path("x.wav"), language="zh", hotwords=[])

    assert len(CALLS["batch"]) == 1
    assert CALLS["batch"][0]["batch_size"] == 8
    assert CALLS["batch"][0]["without_timestamps"] is False
    assert CALLS["normal"] == []
    assert utterances[0].text == "hello"


def test_batch_size_one_stays_single(monkeypatch) -> None:
    CALLS["normal"].clear()
    CALLS["batch"].clear()
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeModel, BatchedInferencePipeline=FakeBatch),
    )
    monkeypatch.setattr(fw, "_audio_input", lambda path: "audio")

    _provider(1).transcribe(Path("x.wav"), language="zh", hotwords=[])

    assert len(CALLS["normal"]) == 1
    assert CALLS["batch"] == []


def test_missing_batched_pipeline_falls_back(monkeypatch) -> None:
    CALLS["normal"].clear()
    CALLS["batch"].clear()
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeModel),
    )
    monkeypatch.setattr(fw, "_audio_input", lambda path: "audio")

    _provider(8).transcribe(Path("x.wav"), language="zh", hotwords=[])

    assert len(CALLS["normal"]) == 1
    assert CALLS["batch"] == []
