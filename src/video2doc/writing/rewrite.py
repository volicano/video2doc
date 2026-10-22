"""只重写某一节，其余章节保持原样。"""

from __future__ import annotations

from ..config import WriteConfig
from ..errors import StageError
from ..schema import Document, Segmentation, Transcript, VisualMoment, WrittenSection
from .materials import assign_moments
from .write import ProgressFn, write_section


def rewrite_section(
    document: Document,
    segmentation: Segmentation,
    transcript: Transcript,
    moments: list[VisualMoment],
    provider,
    config: WriteConfig,
    *,
    section_index: int,
    language: str,
    hint: str = "",
    progress: ProgressFn | None = None,
) -> tuple[Document, WrittenSection]:
    """重新生成第 `section_index` 节，返回更新后的文档和这一节的新内容。"""
    target = next(
        (section for section in segmentation.sections if section.index == section_index), None
    )
    if target is None:
        available = ", ".join(str(section.index) for section in segmentation.sections) or "无"
        raise StageError(
            f"没有第 {section_index} 节",
            hint=f"可选章节：{available}",
        )
    if not any(section.index == section_index for section in document.sections):
        raise StageError(
            f"当前文档里没有第 {section_index} 节",
            hint="先跑一次完整的 run，或换一个章节号。",
        )

    buckets = assign_moments(segmentation.sections, moments)
    written = write_section(
        target,
        transcript.utterances,
        buckets.get(section_index, []),
        provider,
        config,
        language=language,
        progress=progress,
        hint=hint,
        want_title=True,
    )
    written = written.model_copy(update={"rewritten": True})

    sections = [
        written if section.index == section_index else section
        for section in document.sections
    ]
    updated = document.model_copy(
        update={
            "sections": sections,
            "chars": sum(section.chars for section in sections),
            "images": sum(len(section.images) for section in sections),
        }
    )
    return updated, written
