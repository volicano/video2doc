"""单节重写：CLI 的 `v2doc rewrite` 和本地校对界面共用这一层。

职责边界：读上游产物 → 调一次模型 → 把新的 `stages/write.json` 落盘 →
更新任务用量和阶段摘要。质检和渲染由调用方接着跑。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .config import Config
from .errors import StageError
from .job import save_job
from .pipeline import Context
from .providers.llm.base import fold_usage, reset_usage
from .schema import Alignment, Document, Job, Segmentation, WrittenSection
from .writing import load_transcript, make_llm, resolve_language
from .writing.rewrite import ProgressFn, rewrite_section

WRITE_OUTPUT = Path("stages") / "write.json"


@dataclass
class Revision:
    document: Document
    section: WrittenSection
    previous: WrittenSection | None

    @property
    def delta_chars(self) -> int:
        return self.section.chars - (self.previous.chars if self.previous else 0)

    def summary(self) -> str:
        section = self.section
        return f"第 {section.index} 节 · {section.chars} 字 · {len(section.images)} 张图"


def regenerate_section(
    job: Job,
    job_dir: Path,
    config: Config,
    *,
    section_index: int,
    hint: str = "",
    progress: ProgressFn | None = None,
) -> Revision:
    """只重写 `section_index` 这一节，其余章节和导语保持不变。"""
    ctx = Context(job, job_dir, config, Console())
    segmentation = ctx.load("segment", Segmentation)
    transcript = load_transcript(ctx)
    alignment = ctx.load("align", Alignment)
    document = ctx.load("write", Document)

    previous = next((item for item in document.sections if item.index == section_index), None)
    if previous is None:
        available = ", ".join(str(item.index) for item in document.sections) or "无"
        raise StageError(
            f"当前文档里没有第 {section_index} 节",
            hint=f"可选章节：{available}",
        )
    if not any(item.index == section_index for item in segmentation.sections):
        raise StageError(
            f"分段结果里没有第 {section_index} 节",
            hint="加上 --from segment 重新分段后再试。",
        )

    reset_usage()
    provider = make_llm(config, config.write.temperature)
    try:
        updated, written = rewrite_section(
            document,
            segmentation,
            transcript,
            alignment.moments,
            provider,
            config.write,
            section_index=section_index,
            language=resolve_language(config, transcript),
            hint=hint,
            progress=progress,
        )
        _write_document(job_dir, updated)
        _update_record(job, updated)
        return Revision(document=updated, section=written, previous=previous)
    finally:
        # 即使中途失败，已经花掉的 token 也要记进任务
        fold_usage(job.usage)
        save_job(job, job_dir)


def _write_document(job_dir: Path, document: Document) -> None:
    path = job_dir / WRITE_OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _update_record(job: Job, document: Document) -> None:
    record = job.stages.get("write")
    if record is not None:
        record.summary = document.summary()
