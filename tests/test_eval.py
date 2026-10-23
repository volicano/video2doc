from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from video2doc.cli import app
from video2doc.errors import Video2DocError
from video2doc.evaluate import evaluate_job
from video2doc.schema import (
    Critique,
    Document,
    FrameText,
    ImageRef,
    Job,
    OCRResult,
    SourceInfo,
    StageRecord,
    Transcript,
    Utterance,
    WrittenSection,
)


def _write_job(
    directory: Path,
    *,
    duration: float = 120.0,
    sections: list[WrittenSection] | None = None,
    transcript: Transcript | None = None,
    ocr: OCRResult | None = None,
    critique: Critique | None = None,
    html: str = "<html><body><figure></figure></body></html>",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stages_dir = directory / "stages"
    stages_dir.mkdir(exist_ok=True)

    if sections is None:
        sections = [
            WrittenSection(
                index=1,
                title="第一节",
                start=0,
                end=60,
                markdown="## 第一节\n\n这是第一节的正文，[[img:1]]。",
                images=[ImageRef(id=1, time=10, image="frames/a.jpg")],
                chars=120,
            ),
            WrittenSection(
                index=2,
                title="第二节",
                start=60,
                end=duration,
                markdown="## 第二节\n\n这是第二节的正文，[[img:1]]。",
                images=[ImageRef(id=1, time=90, image="frames/b.jpg")],
                chars=110,
            ),
        ]

    document = Document(
        title="测试教程",
        intro="导语",
        duration=duration,
        sections=sections,
        chars=sum(section.chars for section in sections),
        images=sum(len(section.images) for section in sections),
    )
    (stages_dir / "write.json").write_text(document.model_dump_json(), encoding="utf-8")

    stages = {
        "ingest": StageRecord(name="ingest", status="done", fingerprint="f", output=None),
        "write": StageRecord(
            name="write", status="done", fingerprint="f", output="stages/write.json"
        ),
    }
    if transcript is not None:
        (stages_dir / "transcript.json").write_text(transcript.model_dump_json(), encoding="utf-8")
        stages["transcript"] = StageRecord(
            name="transcript", status="done", fingerprint="f", output="stages/transcript.json"
        )
    if ocr is not None:
        (stages_dir / "ocr.json").write_text(ocr.model_dump_json(), encoding="utf-8")
        stages["ocr"] = StageRecord(
            name="ocr", status="done", fingerprint="f", output="stages/ocr.json"
        )
    if critique is not None:
        (stages_dir / "critique.json").write_text(critique.model_dump_json(), encoding="utf-8")
        stages["critique"] = StageRecord(
            name="critique", status="done", fingerprint="f", output="stages/critique.json"
        )

    job = Job(
        job_id="BVtest",
        target="https://example.com/test",
        created_at="2026-10-22T09:00:00",
        updated_at="2026-10-22T10:00:00",
        source=SourceInfo(kind="url", path="", title="测试视频", duration=duration),
        stages=stages,
    )
    job.usage.add(1000, 200)
    (directory / "job.json").write_text(job.model_dump_json(), encoding="utf-8")
    (directory / "tutorial.html").write_text(html, encoding="utf-8")
    return directory


def _transcript(duration: float = 120.0) -> Transcript:
    return Transcript(
        source="asr",
        provider="test",
        duration=duration,
        utterances=[Utterance(start=0, end=duration, text="测试转写")],
    )


def test_evaluate_job_reports_structure(tmp_path: Path) -> None:
    job_dir = _write_job(
        tmp_path / "job",
        transcript=_transcript(),
        ocr=OCRResult(frames=[FrameText(index=1, text="普通画面", char_count=4)]),
        critique=Critique(coverage=0.9, images_used=2, images_available=2),
    )

    report = evaluate_job(job_dir)

    assert report.sections == 2
    assert report.chars == 230
    assert report.images == 2
    assert report.timestamp_errors == 0
    assert report.time_coverage == 1.0
    assert report.missing_image_refs == 0
    assert report.transcript_utterances == 1
    assert report.transcript_coverage == 1.0
    assert report.llm_calls == 1
    assert report.html_bytes > 0
    assert report.passed is True
    assert report.warnings == []


def test_evaluate_job_flags_broken_refs_timestamps_and_code(tmp_path: Path) -> None:
    bad_section = WrittenSection(
        index=1,
        title="坏章节",
        start=0,
        end=200,
        markdown="## 坏章节\n\n这里引用了不存在的图，[[img:2]]。",
        images=[ImageRef(id=1, time=10, image="frames/a.jpg")],
        chars=60,
    )
    ocr = OCRResult(
        frames=[
            FrameText(index=1, text="def foo():\n    return 1", char_count=20),
            FrameText(index=2, text="const x = 1;", char_count=10),
            FrameText(index=3, text="function bar() { return 2; }", char_count=20),
        ]
    )
    job_dir = _write_job(
        tmp_path / "bad",
        duration=120,
        sections=[bad_section],
        ocr=ocr,
    )

    report = evaluate_job(job_dir)

    assert report.timestamp_errors == 1
    assert report.missing_image_refs == 1
    assert report.short_sections == 1
    assert report.code_frames == 3
    assert report.code_blocks == 0
    assert report.passed is False
    assert any("不存在" in warning for warning in report.warnings)
    assert any("代码" in warning for warning in report.warnings)


def test_eval_cli_outputs_json(tmp_path: Path) -> None:
    job_dir = _write_job(
        tmp_path / "job",
        transcript=_transcript(),
        ocr=OCRResult(frames=[FrameText(index=1, text="普通画面", char_count=4)]),
        critique=Critique(coverage=0.9),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["eval", str(job_dir), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["job_id"] == "BVtest"
    assert payload["sections"] == 2
    assert payload["passed"] is True


def test_eval_cli_strict_returns_nonzero_on_warnings(tmp_path: Path) -> None:
    job_dir = _write_job(
        tmp_path / "bad",
        sections=[
            WrittenSection(
                index=1,
                title="空章节",
                start=0,
                end=120,
                markdown="",
                chars=0,
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(app, ["eval", str(job_dir), "--strict"])

    assert result.exit_code == 1
    assert "警告" in result.stdout


def test_evaluate_job_requires_write_stage(tmp_path: Path) -> None:
    job_dir = tmp_path / "empty"
    job_dir.mkdir()
    (job_dir / "job.json").write_text(
        Job(
            job_id="BVempty",
            target="https://example.com/empty",
            created_at="2026-10-22T09:00:00",
            updated_at="2026-10-22T09:00:00",
        ).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(Video2DocError, match="write"):
        evaluate_job(job_dir)
