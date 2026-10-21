from __future__ import annotations

import io
from pathlib import Path

from pydantic import BaseModel
from rich.console import Console

from video2doc.config import Config
from video2doc.errors import StageError
from video2doc.pipeline import Context, Pipeline, Stage
from video2doc.schema import Job
from video2doc.utils.common import now_iso


class Payload(BaseModel):
    value: str


class RecordingStage(Stage):
    name = "first"
    title = "first"
    version = 1

    def __init__(self, key: str = "a") -> None:
        self.calls = 0
        self.key = key

    def params(self, config: Config) -> dict:
        return {"key": self.key}

    def run(self, ctx: Context) -> BaseModel:
        self.calls += 1
        return Payload(value=f"run-{self.calls}")

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, Payload)
        return result.value


class SecondStage(Stage):
    name = "second"
    title = "second"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, ctx: Context) -> BaseModel:
        self.calls += 1
        return Payload(value="second")


class SkippingStage(Stage):
    name = "maybe"
    title = "maybe"

    def skip(self, ctx: Context) -> str | None:
        return "没有需要做的事"

    def run(self, ctx: Context) -> BaseModel:  # pragma: no cover - 不应被调用
        raise AssertionError("skip 生效时不应执行 run")


class FailingStage(Stage):
    name = "boom"
    title = "boom"

    def run(self, ctx: Context) -> BaseModel:
        raise StageError("炸了", hint="换个姿势")


def _job() -> Job:
    return Job(job_id="t", target="fake.mp4", created_at=now_iso(), updated_at=now_iso())


def _console() -> Console:
    return Console(file=io.StringIO(), width=120, force_terminal=False)


def test_second_run_hits_cache(tmp_path: Path) -> None:
    stage = RecordingStage()
    job = _job()
    first = Pipeline([stage], job, tmp_path, Config(), _console()).run()
    assert stage.calls == 1
    assert first.stages["first"].status == "done"
    assert (tmp_path / "stages" / "first.json").exists()

    Pipeline([stage], job, tmp_path, Config(), _console()).run()
    assert stage.calls == 1


def test_param_change_invalidates_cache_and_downstream(tmp_path: Path) -> None:
    first = RecordingStage()
    second = SecondStage()
    job = _job()
    Pipeline([first, second], job, tmp_path, Config(), _console()).run()
    assert (first.calls, second.calls) == (1, 1)

    changed = RecordingStage(key="b")
    Pipeline([changed, second], job, tmp_path, Config(), _console()).run()
    assert changed.calls == 1
    assert second.calls == 2  # 上游指纹变了，下游也必须重跑


def test_force_reruns(tmp_path: Path) -> None:
    stage = RecordingStage()
    job = _job()
    Pipeline([stage], job, tmp_path, Config(), _console()).run()
    Pipeline([stage], job, tmp_path, Config(), _console(), force=True).run()
    assert stage.calls == 2


def test_from_stage_forces_that_stage(tmp_path: Path) -> None:
    first = RecordingStage()
    second = SecondStage()
    job = _job()
    Pipeline([first, second], job, tmp_path, Config(), _console()).run()
    Pipeline([first, second], job, tmp_path, Config(), _console(), start_at="second").run()
    assert first.calls == 1
    assert second.calls == 2


def test_skip_is_recorded(tmp_path: Path) -> None:
    job = _job()
    result = Pipeline([SkippingStage()], job, tmp_path, Config(), _console()).run()
    assert result.stages["maybe"].status == "skipped"
    assert "没有需要做的事" in result.stages["maybe"].summary


def test_failure_is_recorded_and_raised(tmp_path: Path) -> None:
    job = _job()
    try:
        Pipeline([FailingStage()], job, tmp_path, Config(), _console()).run()
    except StageError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应该抛出 StageError")

    assert job.stages["boom"].status == "failed"
    assert "炸了" in (job.stages["boom"].error or "")


def test_job_is_persisted_between_runs(tmp_path: Path) -> None:
    job = _job()
    Pipeline([RecordingStage()], job, tmp_path, Config(), _console()).run()
    reloaded = Job.model_validate_json((tmp_path / "job.json").read_text(encoding="utf-8"))
    assert reloaded.stages["first"].status == "done"
