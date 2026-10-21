"""流水线编排：缓存、断点续跑、错误包装。

每个阶段的指纹 = hash(阶段名 + 版本 + 参数 + 源标识 + 上游指纹)。
只要参数或上游产物变了，指纹就变，这个阶段和它之后的所有阶段自动重跑；
没变则秒过。这是整个项目能持续迭代的前提。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.markup import escape
from rich.status import Status

from .config import Config
from .errors import StageError, Video2DocError
from .job import save_job
from .schema import Job, StageRecord
from .utils.common import fingerprint, now_iso

T = TypeVar("T", bound=BaseModel)


class Context:
    """阶段运行时的上下文。"""

    def __init__(
        self,
        job: Job,
        job_dir: Path,
        config: Config,
        console: Console,
        *,
        status: Status | None = None,
    ) -> None:
        self.job = job
        self.job_dir = job_dir
        self.config = config
        self.console = console
        self.status = status

    def path(self, *parts: str) -> Path:
        path = self.job_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def progress(self, message: str) -> None:
        if self.status is not None:
            self.status.update(f"[cyan]{escape(message)}[/]")
        else:
            self.console.print(f"   [dim]{escape(message)}[/]")

    def detail(self, message: str) -> None:
        self.console.print(f"   [dim]{escape(message)}[/]")

    def load(self, stage_name: str, model: type[T]) -> T:
        record = self.job.stages.get(stage_name)
        if record is None or record.status != "done" or not record.output:
            raise StageError(
                f"缺少上游产物：{stage_name}",
                hint=f"先跑完 {stage_name} 阶段（去掉 --from 参数重试）。",
            )
        path = self.job_dir / record.output
        if not path.exists():
            raise StageError(
                f"上游产物文件丢失：{path}",
                hint="删掉任务目录重跑，或加上 --force。",
            )
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise StageError(
                f"上游产物格式不符：{stage_name}",
                hint="可能是旧版本产物，加上 --force 重跑。",
            ) from exc


class Stage(ABC):
    """一个阶段 = 一个纯函数式步骤：读上游产物，写自己的产物。"""

    name: str = ""
    title: str = ""
    version: int = 1
    requires: tuple[str, ...] = ()

    def params(self, config: Config) -> dict[str, Any]:
        """影响本阶段结果的所有配置项。加了这个才算进缓存指纹。"""
        return {}

    def skip(self, ctx: Context) -> str | None:
        """返回原因则跳过（例如已有字幕就不用跑 ASR）。"""
        return None

    @abstractmethod
    def run(self, ctx: Context) -> BaseModel: ...

    def summary(self, result: BaseModel) -> str:
        return ""


@dataclass
class Pipeline:
    stages: list[Stage]
    job: Job
    job_dir: Path
    config: Config
    console: Console
    force: bool = False
    start_at: str | None = None
    _names: list[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self._names = [stage.name for stage in self.stages]
        duplicates = {name for name in self._names if self._names.count(name) > 1}
        if duplicates:
            raise ValueError(f"阶段名重复：{duplicates}")
        if self.start_at and self.start_at not in self._names:
            raise StageError(
                f"未知阶段：{self.start_at}",
                hint=f"可用阶段：{', '.join(self._names)}",
            )

    # ------------------------------------------------------------------ run

    def run(self) -> Job:
        upstream = ""
        start_index = self._names.index(self.start_at) if self.start_at else -1
        total = len(self.stages)

        for index, stage in enumerate(self.stages):
            stage_fp = fingerprint(
                {
                    "stage": stage.name,
                    "version": stage.version,
                    "params": stage.params(self.config),
                    "source": _source_key(self.job.target),
                    "upstream": upstream,
                }
            )
            upstream = stage_fp
            forced = self.force or index >= start_index >= 0

            record = self.job.stages.get(stage.name)
            if not forced and self._cache_hit(record, stage_fp):
                assert record is not None
                if record.status == "skipped":
                    self.console.print(
                        f"[yellow]–[/] [bold]{stage.name:<12}[/] "
                        f"[dim]{escape(record.summary)}（缓存）[/]"
                    )
                else:
                    self.console.print(
                        f"[dim]↻ {stage.name:<12} 命中缓存 · {escape(record.summary)}[/]"
                    )
                continue

            self.console.print(
                f"[bold cyan]{index + 1}/{total}[/] [bold]{stage.name:<12}[/] "
                f"[dim]{escape(stage.title)}[/]"
            )

            ctx = Context(self.job, self.job_dir, self.config, self.console)
            reason = stage.skip(ctx)
            if reason:
                self.job.stages[stage.name] = StageRecord(
                    name=stage.name,
                    status="skipped",
                    fingerprint=stage_fp,
                    summary=reason,
                    started_at=now_iso(),
                    finished_at=now_iso(),
                )
                save_job(self.job, self.job_dir)
                self.console.print(f"[yellow]–[/] {stage.name:<12} 跳过：{escape(reason)}")
                continue

            result, elapsed = self._execute(stage, ctx, stage_fp)
            summary = stage.summary(result)
            output_rel = f"stages/{stage.name}.json"
            output_path = self.job_dir / output_rel
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

            self.job.stages[stage.name] = StageRecord(
                name=stage.name,
                status="done",
                fingerprint=stage_fp,
                output=output_rel,
                summary=summary,
                started_at=now_iso(),
                finished_at=now_iso(),
                elapsed=round(elapsed, 2),
            )
            save_job(self.job, self.job_dir)
            self.console.print(f"[green]✓[/] {stage.name:<12} [dim]{escape(summary)}[/]")

        return self.job

    # -------------------------------------------------------------- helpers

    def _execute(self, stage: Stage, ctx: Context, stage_fp: str) -> tuple[BaseModel, float]:
        started = time.perf_counter()
        try:
            with self.console.status(f"[cyan]{escape(stage.title or stage.name)}[/]") as status:
                ctx.status = status
                try:
                    result = stage.run(ctx)
                finally:
                    ctx.status = None
            return result, time.perf_counter() - started
        except Video2DocError as exc:
            self._record_failure(stage, stage_fp, exc.message)
            raise
        except KeyboardInterrupt:
            self._record_failure(stage, stage_fp, "用户中断")
            raise
        except Exception as exc:  # noqa: BLE001
            self._record_failure(stage, stage_fp, f"{type(exc).__name__}: {exc}")
            raise StageError(
                f"{stage.name} 阶段意外失败：{type(exc).__name__}: {exc}",
                hint="加上 --force 重跑；若反复失败请把上面的报错贴到 issue。",
            ) from exc

    def _record_failure(self, stage: Stage, stage_fp: str, message: str) -> None:
        self.job.stages[stage.name] = StageRecord(
            name=stage.name,
            status="failed",
            fingerprint=stage_fp,
            error=message[:2000],
            started_at=now_iso(),
            finished_at=now_iso(),
        )
        save_job(self.job, self.job_dir)

    def _cache_hit(self, record: StageRecord | None, stage_fp: str) -> bool:
        if record is None or record.status not in ("done", "skipped"):
            return False
        if record.fingerprint != stage_fp:
            return False
        if record.status == "done":
            return bool(record.output) and (self.job_dir / record.output).exists()
        return True


def _source_key(target: str) -> str:
    """本地文件把大小和 mtime 算进去：视频被换掉了就该重跑。"""
    path = Path(target).expanduser()
    try:
        if path.is_file():
            stat = path.stat()
            return f"file:{path.resolve()}:{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        pass
    return f"target:{target}"
