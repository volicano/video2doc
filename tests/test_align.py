from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from rich.console import Console

from video2doc.config import Config
from video2doc.pipeline import Context
from video2doc.schema import (
    FrameCandidate,
    FrameSelection,
    FrameText,
    Job,
    OCRResult,
    SourceInfo,
    StageRecord,
    Transcript,
    Utterance,
    VisualMoment,
)
from video2doc.stages.align import (
    _coverage,
    _gaps,
    _join_text,
    _nearest,
    _refine_kind,
    _spoken,
    _window,
)
from video2doc.utils.common import now_iso


def _job_dir(tmp_path: Path) -> Job:
    (tmp_path / "stages").mkdir(parents=True)
    (tmp_path / "frames").mkdir()

    image = Image.new("RGB", (320, 180), "white")
    image.save(tmp_path / "frames" / "f1.jpg")

    selection = FrameSelection(
        strategy="info-density",
        interval=1.0,
        sampled=100,
        shots=10,
        duration=100.0,
        frames=[
            FrameCandidate(
                index=1,
                time=4.0,
                path="frames/f1.jpg",
                shot_start=0.0,
                shot_end=10.0,
                kind="slide",
                score=0.31,
            )
        ],
    )
    transcript = Transcript(
        source="subtitles",
        provider="subtitle:vtt",
        duration=100.0,
        utterances=[
            Utterance(start=0.0, end=3.0, text="第一句"),
            Utterance(start=3.0, end=6.0, text="第二句"),
            Utterance(start=40.0, end=44.0, text="后面的话"),
        ],
    )
    source = SourceInfo(kind="file", path="fake.mp4", title="测试视频", duration=100.0)
    ocr = OCRResult(
        provider="fake",
        frames=[
            FrameText(index=1, text="def foo(): return {x}(); y = z", char_count=26),
            FrameText(index=2, text="", char_count=0),
        ],
    )

    for name, model in (
        ("frames", selection),
        ("transcript", transcript),
        ("ingest", source),
        ("ocr", ocr),
    ):
        (tmp_path / "stages" / f"{name}.json").write_text(
            model.model_dump_json(indent=2), encoding="utf-8"
        )

    job = Job(job_id="t", target="fake.mp4", created_at=now_iso(), updated_at=now_iso())
    for name in ("ingest", "transcript", "frames", "ocr"):
        job.stages[name] = StageRecord(
            name=name,
            status="done",
            fingerprint="x",
            output=f"stages/{name}.json",
        )
    return job


# ------------------------------------------------------------------ 纯函数


def test_spoken_overlap() -> None:
    texts = [
        Utterance(start=0, end=2, text="a"),
        Utterance(start=2, end=4, text="b"),
        Utterance(start=10, end=12, text="c"),
    ]
    assert [item.text for item in _spoken(texts, 1.0, 3.0)] == ["a", "b"]
    assert _spoken(texts, 20.0, 30.0) == []


def test_nearest_borrows_both_sides() -> None:
    texts = [Utterance(start=index * 10, end=index * 10 + 4, text=str(index)) for index in range(6)]
    picked = _nearest(texts, 22.0, 26.0, span=15.0)
    assert [item.text for item in picked] == ["1", "3", "4"]


def test_join_text_respects_language() -> None:
    assert _join_text(["你好，", "世界"]) == "你好，世界"
    assert _join_text(["hello", "world"]) == "hello world"
    assert _join_text([]) == ""


def test_refine_kind_detects_code() -> None:
    assert _refine_kind("slide", "for (int i = 0; i < n; i++) { sum += i; }") == "code"
    assert (
        _refine_kind("screen", "这是一个很长的说明文字，用来测试是否会从 screen 提升为 slide。")
        == "slide"
    )
    assert _refine_kind("face", "") == "face"


def test_window_falls_back_for_zero_length_shot() -> None:
    frame = FrameCandidate(index=1, time=30.0, path="x.jpg", shot_start=30.0, shot_end=30.0)
    start, end = _window(frame, 100.0)
    assert (start, end) == (25.0, 35.0)


def test_gaps_and_coverage() -> None:
    moments = [
        VisualMoment(index=1, image="a.jpg", time=5, start=0, end=10),
        VisualMoment(index=2, image="b.jpg", time=205, start=200, end=210),
    ]
    assert _coverage(moments, 300.0) == 0.0667
    assert _gaps(moments, 300.0) == [[10.0, 200.0], [210.0, 300.0]]


# ------------------------------------------------------------------ 整段对齐


def test_align_stage_builds_moments_and_preview(tmp_path: Path) -> None:
    job = _job_dir(tmp_path)
    ctx = Context(job, tmp_path, Config(), Console(file=io.StringIO()))

    from video2doc.stages.align import AlignStage

    result = AlignStage().run(ctx)

    assert len(result.moments) == 1
    moment = result.moments[0]
    assert moment.spoken_text == "第一句第二句"
    assert moment.kind == "code"
    assert moment.utterances == 2
    assert moment.extended is False
    assert result.coverage == 0.1
    assert result.debug_html == "preview.html"

    preview = tmp_path / "preview.html"
    assert preview.exists()
    html = preview.read_text(encoding="utf-8")
    assert "第一句第二句" in html
    assert "def foo()" in html


def test_align_stage_extends_speech_when_shot_is_silent(tmp_path: Path) -> None:
    job = _job_dir(tmp_path)
    # 把画面挪到 40s 附近，镜头里没有语音，应该向前借一句
    frames = FrameSelection.model_validate_json(
        (tmp_path / "stages" / "frames.json").read_text(encoding="utf-8")
    )
    frames.frames[0].time = 58.0
    frames.frames[0].shot_start = 55.0
    frames.frames[0].shot_end = 65.0
    (tmp_path / "stages" / "frames.json").write_text(
        frames.model_dump_json(indent=2), encoding="utf-8"
    )

    ctx = Context(job, tmp_path, Config(), Console(file=io.StringIO()))
    from video2doc.stages.align import AlignStage

    moment = AlignStage().run(ctx).moments[0]

    assert moment.extended is True
    assert "后面的话" in moment.spoken_text
