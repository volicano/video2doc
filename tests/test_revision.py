from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from video2doc.config import Config
from video2doc.errors import StageError
from video2doc.job import save_job
from video2doc.revision import regenerate_section
from video2doc.schema import (
    Alignment,
    Document,
    Job,
    Section,
    Segmentation,
    StageRecord,
    Transcript,
    Utterance,
    VisualMoment,
)
from video2doc.utils.common import now_iso
from video2doc.writing.write import write_document


class FakeLLM:
    def __init__(self, payload: dict, *, title: str = "") -> None:
        self.payload = payload
        self.title = title
        self.prompts: list[str] = []
        self.config = SimpleNamespace(model="fake")

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        self.prompts.append(user)
        data = dict(self.payload)
        if self.title:
            data["title"] = self.title
        return data


def _transcript() -> Transcript:
    return Transcript(
        source="asr",
        provider="fake",
        duration=200.0,
        utterances=[
            Utterance(start=index * 10.0, end=index * 10.0 + 10.0, text=f"第 {index} 句。")
            for index in range(20)
        ],
    )


def _segmentation() -> Segmentation:
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


def _moments() -> list[VisualMoment]:
    return [
        VisualMoment(
            index=1,
            image="frames/f01.jpg",
            time=10.0,
            start=0.0,
            end=40.0,
            kind="slide",
            score=0.5,
        ),
        VisualMoment(
            index=2,
            image="frames/f02.jpg",
            time=120.0,
            start=110.0,
            end=150.0,
            kind="slide",
            score=0.6,
        ),
    ]


def _document() -> Document:
    llm = FakeLLM({"summary": "摘要", "markdown": "原文正文。\n\n[[img:1]]"})
    return write_document(
        _segmentation(),
        _transcript(),
        _moments(),
        llm,
        Config().write,
        title="原始标题",
        language="zh",
    )


def _write_json(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _job_dir(tmp_path: Path) -> tuple[Job, Path]:
    _write_json(tmp_path / "stages" / "segment.json", _segmentation())
    _write_json(tmp_path / "stages" / "transcript.json", _transcript())
    _write_json(tmp_path / "stages" / "align.json", Alignment(duration=200.0, moments=_moments()))
    _write_json(tmp_path / "stages" / "write.json", _document())

    job = Job(job_id="t", target="x.mp4", created_at=now_iso(), updated_at=now_iso())
    for name in ("segment", "transcript", "align"):
        job.stages[name] = StageRecord(
            name=name, status="done", fingerprint="x", output=f"stages/{name}.json", summary="fake"
        )
    job.stages["write"] = StageRecord(
        name="write", status="done", fingerprint="x", output="stages/write.json", summary="fake"
    )
    save_job(job, tmp_path)
    return job, tmp_path


def test_regenerate_section_updates_only_target_and_persists(tmp_path, monkeypatch) -> None:
    job, job_dir = _job_dir(tmp_path)
    before = Document.model_validate_json(
        (job_dir / "stages" / "write.json").read_text(encoding="utf-8")
    )
    first_before = before.sections[0].model_dump()

    fake = FakeLLM(
        {"summary": "新摘要", "markdown": "重写后的第二节。\n\n[[img:2]]"},
        title="新的第二节",
    )
    monkeypatch.setattr("video2doc.revision.make_llm", lambda cfg, temp=None: fake)

    folded: list[int] = []
    monkeypatch.setattr(
        "video2doc.revision.fold_usage", lambda usage: folded.append(usage.calls)
    )

    revision = regenerate_section(
        job, job_dir, Config(), section_index=2, hint="重点是任务接管"
    )

    assert revision.section.index == 2
    assert revision.section.title == "新的第二节"
    assert revision.section.rewritten is True
    assert revision.previous is not None and revision.previous.markdown.startswith("原文正文")
    assert "重点是任务接管" in fake.prompts[0]
    assert folded == [0]

    saved = Document.model_validate_json(
        (job_dir / "stages" / "write.json").read_text(encoding="utf-8")
    )
    assert saved.sections[0].model_dump() == first_before
    assert saved.sections[1].markdown.startswith("重写后的第二节。")
    assert saved.sections[1].title == "新的第二节"

    reloaded = Job.model_validate_json((job_dir / "job.json").read_text(encoding="utf-8"))
    assert reloaded.stages["write"].summary == saved.summary()


def test_regenerate_section_clears_only_that_override(tmp_path, monkeypatch) -> None:
    job, job_dir = _job_dir(tmp_path)
    (job_dir / "overrides.json").write_text(
        json.dumps(
            {
                "title": "人工标题",
                "sections": {
                    "1": {"title": "人工第一节"},
                    "2": {"title": "人工第二节", "markdown": "人工正文"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "video2doc.revision.make_llm",
        lambda cfg, temp=None: FakeLLM({"summary": "新摘要", "markdown": "重写后的第二节。"}),
    )

    regenerate_section(job, job_dir, Config(), section_index=2)

    saved = json.loads((job_dir / "overrides.json").read_text(encoding="utf-8"))
    # 第 2 节被模型重写了，旧覆盖必须清掉，否则渲染时看不到新内容
    assert "2" not in saved["sections"]
    # 其他章节和教程标题不受影响
    assert saved["sections"]["1"]["title"] == "人工第一节"
    assert saved["title"] == "人工标题"


def test_regenerate_section_rejects_unknown_section(tmp_path, monkeypatch) -> None:
    job, job_dir = _job_dir(tmp_path)
    monkeypatch.setattr(
        "video2doc.revision.make_llm",
        lambda cfg, temp=None: FakeLLM({"summary": "", "markdown": ""}),
    )

    try:
        regenerate_section(job, job_dir, Config(), section_index=9)
    except StageError as exc:
        assert "没有第 9 节" in exc.message
        assert "1, 2" in (exc.hint or "")
    else:  # pragma: no cover - 失败提示
        raise AssertionError("应当拒绝不存在的章节")
