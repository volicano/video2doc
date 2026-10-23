from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from video2doc.config import ASRConfig
from video2doc.errors import StageError
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

    def transcribe(self, audio, hotwords=None, **kwargs):
        CALLS["normal"].append({"hotwords": hotwords, **kwargs})
        return iter([FakeSegment()]), FakeInfo()


class FakeBatch:
    def __init__(self, model) -> None:
        pass

    def transcribe(self, audio, hotwords=None, **kwargs):
        CALLS["batch"].append({"hotwords": hotwords, **kwargs})
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


def test_native_hotwords_are_passed(monkeypatch) -> None:
    CALLS["normal"].clear()
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeModel),
    )
    monkeypatch.setattr(fw, "_audio_input", lambda path: "audio")

    _provider(1).transcribe(Path("x.wav"), language="zh", hotwords=["EgoLite", "EgoBrowser"])

    assert CALLS["normal"][0]["hotwords"] == "EgoLite EgoBrowser"


def test_cuda_load_failure_falls_back_to_cpu(monkeypatch) -> None:
    CALLS["normal"].clear()
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeModel),
    )
    monkeypatch.setattr(fw, "_audio_input", lambda path: "audio")
    monkeypatch.setattr(fw, "_pick_device", lambda requested: "cuda")
    monkeypatch.setattr(
        fw,
        "_pick_compute",
        lambda device, requested: "float16" if device == "cuda" else "int8",
    )

    def fake_load(model_cls, model_name, device, compute_type):
        if device == "cuda":
            raise StageError("CUDA 运行库缺失")
        return FakeModel()

    monkeypatch.setattr(fw, "_load_model", fake_load)
    provider = fw.FasterWhisperProvider(ASRConfig(device="cuda", model="large-v3"))
    progress: list[str] = []

    utterances = provider.transcribe(
        Path("x.wav"), language="zh", hotwords=[], progress=progress.append
    )

    assert len(CALLS["normal"]) == 1
    assert any("回退 CPU" in message for message in progress)
    assert utterances[0].text == "hello"


def test_model_retry_failure_wraps_stage_error() -> None:
    class BrokenModel:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("Library cublas64_12.dll is not found")

    with pytest.raises(StageError):
        fw._load_model(BrokenModel, "large-v3", "cuda", "float16")
