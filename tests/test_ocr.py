from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from video2doc.config import Config, OCRConfig
from video2doc.pipeline import Context
from video2doc.providers.ocr import base as ocr_base
from video2doc.providers.ocr.rapidocr import _to_blocks
from video2doc.schema import FrameCandidate, FrameSelection, Job, StageRecord, TextBlock
from video2doc.utils.common import now_iso

RAW = [
    [[[10, 40], [200, 40], [200, 60], [10, 60]], "second line", 0.91],
    [[[10, 10], [100, 10], [100, 30], [10, 30]], "first line", 0.99],
    [[[10, 10], [100, 10], [100, 30], [10, 30]], "   ", 0.88],
    [[[10, 80], [180, 80], [180, 100], [10, 100]], "third", 0.72],
]


def test_to_blocks_sorts_by_line_and_drops_blank() -> None:
    blocks = _to_blocks(RAW)

    assert [block.text for block in blocks] == ["first line", "second line", "third"]
    assert blocks[0].box == [10.0, 10.0, 100.0, 30.0]
    assert blocks[0].confidence == 0.99


def test_resolve_name_handles_auto_and_none() -> None:
    assert ocr_base.resolve_name("none") == "none"
    assert ocr_base.resolve_name("rapidocr") == "rapidocr"
    assert ocr_base.resolve_name("auto") in ("rapidocr", "none")


def test_available_providers_lists_rapidocr() -> None:
    names = {status.name for status in ocr_base.available_providers()}
    assert "rapidocr" in names


class FakeProvider(ocr_base.OCRProvider):
    name = "fake"
    label = "fake"

    def read(self, image: Path) -> list[TextBlock]:
        return [
            TextBlock(text="keep me", confidence=0.9),
            TextBlock(text="noise", confidence=0.2),
        ]


def _job(job_dir: Path, count: int = 1) -> Job:
    selection = FrameSelection(
        strategy="info-density",
        interval=1.0,
        sampled=count,
        shots=count,
        duration=float(count * 5),
        frames=[
            FrameCandidate(
                index=index,
                time=float((index - 1) * 5 + 2),
                path=f"frames/f{index}.jpg",
                shot_start=float((index - 1) * 5),
                shot_end=float(index * 5),
                kind="slide",
            )
            for index in range(1, count + 1)
        ],
    )
    (job_dir / "stages").mkdir(parents=True)
    (job_dir / "frames").mkdir()
    for index in range(1, count + 1):
        (job_dir / "frames" / f"f{index}.jpg").write_bytes(b"not really a jpeg")
    (job_dir / "stages" / "frames.json").write_text(
        selection.model_dump_json(indent=2), encoding="utf-8"
    )

    job = Job(
        job_id="t",
        target="fake.mp4",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    job.stages["frames"] = StageRecord(
        name="frames", status="done", fingerprint="x", output="stages/frames.json"
    )
    return job


def test_stage_filters_low_confidence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setitem(ocr_base._REGISTRY, "fake", FakeProvider)
    config = Config(ocr=OCRConfig(provider="fake", min_confidence=0.5))
    ctx = Context(_job(tmp_path), tmp_path, config, Console(file=io.StringIO()))

    from video2doc.stages.ocr import OCRStage

    result = OCRStage().run(ctx)

    assert result.provider == "fake"
    assert result.frames[0].text == "keep me"
    assert result.frames[0].char_count == 7
    assert result.frames[0].mean_confidence == 0.9
    assert result.frames[0].blocks[0].text == "keep me"


class FlakyProvider(ocr_base.OCRProvider):
    name = "fake"
    label = "fake"
    calls = 0

    def read(self, image: Path) -> list[TextBlock]:
        type(self).calls += 1
        if type(self).calls == 2:
            raise RuntimeError("boom")
        return [TextBlock(text=image.name, confidence=0.9)]


class CountingProvider(ocr_base.OCRProvider):
    name = "fake"
    label = "fake"
    calls = 0

    def read(self, image: Path) -> list[TextBlock]:
        type(self).calls += 1
        return [TextBlock(text=image.name, confidence=0.9)]


class ParallelProvider(CountingProvider):
    parallel_safe = True


def test_stage_reuses_ocr_checkpoint_after_failure(monkeypatch, tmp_path: Path) -> None:
    from video2doc.stages.ocr import OCRStage

    FlakyProvider.calls = 0
    CountingProvider.calls = 0
    monkeypatch.setitem(ocr_base._REGISTRY, "fake", FlakyProvider)
    ctx = Context(
        _job(tmp_path, count=2),
        tmp_path,
        Config(ocr=OCRConfig(provider="fake")),
        Console(file=io.StringIO()),
    )

    with pytest.raises(RuntimeError, match="boom"):
        OCRStage().run(ctx)

    checkpoint = tmp_path / "cache" / "ocr_frames.json"
    assert checkpoint.exists()

    monkeypatch.setitem(ocr_base._REGISTRY, "fake", CountingProvider)
    result = OCRStage().run(ctx)

    assert CountingProvider.calls == 1
    assert [frame.index for frame in result.frames] == [1, 2]
    assert result.frames[1].text == "f2.jpg"
    assert not checkpoint.exists()


def test_stage_parallel_keeps_frame_order(monkeypatch, tmp_path: Path) -> None:
    from video2doc.stages.ocr import OCRStage

    ParallelProvider.calls = 0
    monkeypatch.setitem(ocr_base._REGISTRY, "fake", ParallelProvider)
    ctx = Context(
        _job(tmp_path, count=4),
        tmp_path,
        Config(ocr=OCRConfig(provider="fake", workers=4)),
        Console(file=io.StringIO()),
    )

    result = OCRStage().run(ctx)

    assert ParallelProvider.calls == 4
    assert [frame.index for frame in result.frames] == [1, 2, 3, 4]
    assert [frame.text for frame in result.frames] == ["f1.jpg", "f2.jpg", "f3.jpg", "f4.jpg"]


def test_stage_skips_without_engine(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setitem(ocr_base._REGISTRY, "fake", FakeProvider)
    config = Config(ocr=OCRConfig(provider="none"))
    from video2doc.stages.ocr import OCRStage

    ctx = Context(_job(tmp_path), tmp_path, config, Console(file=io.StringIO()))
    reason = OCRStage().skip(ctx)

    assert reason is not None
    assert "OCR" in reason


def test_stage_summary_ignores_empty_ocr(tmp_path: Path) -> None:
    from video2doc.schema import FrameText, OCRResult
    from video2doc.stages.ocr import OCRStage

    result = OCRResult(
        provider="fake",
        frames=[
            FrameText(index=1, text="", char_count=0),
            FrameText(index=2, text="ab", char_count=2),
        ],
    )
    assert OCRStage().summary(result) == "1/2 张有文字 · 共 2 字"
