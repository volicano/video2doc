from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from rich.console import Console

from video2doc.config import Config, LLMConfig, RenderConfig, WriteConfig
from video2doc.pipeline import Pipeline
from video2doc.schema import (
    Alignment,
    Job,
    SourceInfo,
    StageRecord,
    Transcript,
    Utterance,
    VisualMoment,
)
from video2doc.stages import CritiqueStage, RenderStage, SegmentStage, WriteStage
from video2doc.utils.common import now_iso


class FakeLLM:
    """按系统提示词区分调用：分段 / 写作 / 导语。"""

    config = SimpleNamespace(model="fake")

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, system: str, user: str, *, progress=None) -> dict:
        if "内容编辑" in system:
            self.calls.append("segment")
            return {
                "sections": [
                    {"at": 0, "title": "开场与目标", "kind": "setup"},
                    {"at": 8, "title": "核心演示", "kind": "example"},
                ]
            }
        if "技术教程作者" in system:
            self.calls.append("write")
            return {
                "summary": "这一节讲了一个要点",
                "markdown": "## 要点\n这一节的内容。\n\n[[img:1]]",
            }
        if "导语" in system:
            self.calls.append("intro")
            return {"intro": "这是一段导语。"}
        self.calls.append("other")
        return {"markdown": "修复后的正文"}


def write_json(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def make_job(job_dir: Path) -> Job:
    job = Job(
        job_id="t",
        target="slides.mp4",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    records = {}
    for name in ("ingest", "transcript", "align"):
        records[name] = StageRecord(
            name=name,
            status="done",
            fingerprint="x",
            output=f"stages/{name}.json",
            summary="fake",
        )
    job.stages = records
    return job


def test_m3_pipeline_writes_tutorial(tmp_path: Path, monkeypatch) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (320, 180), (20, 40, 80)).save(frames / "f01.jpg", "JPEG")
    Image.new("RGB", (320, 180), (80, 40, 20)).save(frames / "f02.jpg", "JPEG")

    transcript = Transcript(
        source="asr",
        provider="fake",
        duration=160.0,
        utterances=[
            Utterance(start=index * 10.0, end=index * 10.0 + 10.0, text=f"第 {index} 句讲解。")
            for index in range(16)
        ],
    )
    source = SourceInfo(
        kind="file",
        path="slides.mp4",
        title="测试视频",
        duration=160.0,
    )
    alignment = Alignment(
        duration=160.0,
        coverage=1.0,
        moments=[
            VisualMoment(
                index=1,
                image="frames/f01.jpg",
                time=10.0,
                start=0.0,
                end=60.0,
                kind="slide",
                score=0.5,
                ocr_text="第一页标题",
            ),
            VisualMoment(
                index=2,
                image="frames/f02.jpg",
                time=100.0,
                start=90.0,
                end=150.0,
                kind="code",
                score=0.6,
                ocr_text="function f() { return 1; }",
            ),
        ],
    )

    write_json(tmp_path / "stages" / "transcript.json", transcript)
    write_json(tmp_path / "stages" / "ingest.json", source)
    write_json(tmp_path / "stages" / "align.json", alignment)

    fake = FakeLLM()
    monkeypatch.setattr("video2doc.stages.segment.make_llm", lambda cfg, temp=None: fake)
    monkeypatch.setattr("video2doc.stages.write.make_llm", lambda cfg, temp=None: fake)

    job = make_job(tmp_path)
    console = Console(file=io.StringIO(), width=120, force_terminal=False)
    # 用专用环境变量，别依赖开发机上是否恰好设置了 DEEPSEEK_API_KEY
    monkeypatch.setenv("V2DOC_TEST_KEY", "test-key")
    config = Config(
        llm=LLMConfig(api_key_env="V2DOC_TEST_KEY"),
        write=WriteConfig(max_sections=5),
        render=RenderConfig(embed_images=True, debug_html=False),
    )
    stages = [SegmentStage(), WriteStage(), CritiqueStage(), RenderStage()]

    job = Pipeline(stages, job, tmp_path, config, console).run()

    assert job.stages["segment"].status == "done"
    assert job.stages["write"].status == "done"
    assert job.stages["critique"].status == "done"
    assert job.stages["render"].status == "done"

    html = (tmp_path / "tutorial.html").read_text(encoding="utf-8")
    assert "开场与目标" in html
    assert "核心演示" in html
    assert "data:image/webp;base64," in html
    assert "[[img:" not in html
    assert (tmp_path / "tutorial.md").exists()

    # 相同输入再跑一次：全部命中缓存，不再调用 LLM
    before = len(fake.calls)
    Pipeline(stages, job, tmp_path, config, console).run()
    assert len(fake.calls) == before

    # 只改 overrides.json：render 必须重跑，但仍然不调用 LLM
    (tmp_path / "overrides.json").write_text(
        json.dumps(
            {
                "title": "人工改过的标题",
                "sections": {"2": {"title": "人工第二节"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    job = Pipeline(stages, job, tmp_path, config, console).run()

    assert len(fake.calls) == before
    html = (tmp_path / "tutorial.html").read_text(encoding="utf-8")
    assert "人工改过的标题" in html
    assert "人工第二节" in html


def test_stages_skip_when_api_key_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("V2DOC_NO_SUCH_KEY", raising=False)
    job = Job(job_id="t", target="x.mp4", created_at=now_iso(), updated_at=now_iso())
    config = Config(llm=LLMConfig(api_key_env="V2DOC_NO_SUCH_KEY"))
    console = Console(file=io.StringIO(), width=120, force_terminal=False)
    stages = [SegmentStage(), WriteStage(), CritiqueStage(), RenderStage()]

    job = Pipeline(stages, job, tmp_path, config, console).run()

    for name in ("segment", "write", "critique"):
        assert job.stages[name].status == "skipped"
        assert "V2DOC_NO_SUCH_KEY" in job.stages[name].summary

    # render 不调用模型：没有文档时跳过，但不会因为缺密钥而跳过
    assert job.stages["render"].status == "skipped"
    assert "可渲染" in job.stages["render"].summary
