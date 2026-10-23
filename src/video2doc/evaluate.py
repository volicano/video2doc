"""成稿评测：把「看起来还行」变成一组可比较的数字。

只做确定性检查，不调用模型：

- 时间戳是否落在视频时长内，章节有没有覆盖全片
- 每节配图是否被正文引用，有没有缺号、重复、闲置
- 画面里读到代码时，正文有没有对应代码块
- 转写覆盖率、OCR 覆盖率、质检结果、LLM 用量和耗时

它不判断「写得好不好」，但能挡住最常见的那几类退步。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field

from .errors import Video2DocError
from .overrides import apply_overrides, load_overrides
from .schema import Critique, Document, Job, OCRResult, SourceInfo, Transcript
from .utils.code import looks_like_code
from .writing.materials import count_chars

T = TypeVar("T", bound=BaseModel)

IMG_RE = re.compile(r"\[\[img:(\d+)\]\]", re.IGNORECASE)


class SectionMetric(BaseModel):
    index: int
    title: str
    start: float
    end: float
    chars: int
    images: int
    referenced: int
    missing_refs: int
    unused_images: int


class EvalReport(BaseModel):
    job_id: str
    title: str
    target: str
    duration: float = 0.0
    sections: int = 0
    chars: int = 0
    images: int = 0
    chars_per_minute: float = 0.0

    html_bytes: int = 0
    figures: int = 0
    code_blocks: int = 0
    html_placeholders: int = 0

    section_metrics: list[SectionMetric] = Field(default_factory=list)
    timestamp_errors: int = 0
    time_coverage: float = 0.0
    gaps: list[list[float]] = Field(default_factory=list)
    empty_sections: int = 0
    short_sections: int = 0
    long_sections: int = 0

    missing_image_refs: int = 0
    unused_images: int = 0
    duplicate_images: int = 0

    ocr_frames: int = 0
    ocr_text_frames: int = 0
    ocr_chars: int = 0
    code_frames: int = 0

    transcript_utterances: int = 0
    transcript_chars: int = 0
    transcript_coverage: float = 0.0
    transcript_timestamp_errors: int = 0

    critique_issues: int = 0
    critique_high: int = 0
    critique_coverage: float = 0.0
    rewrites: int = 0

    llm_calls: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    total_elapsed: float = 0.0
    stage_elapsed: dict[str, float] = Field(default_factory=dict)

    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    passed: bool = True

    def summary(self) -> str:
        state = "通过" if self.passed else f"{len(self.warnings)} 条警告"
        return (
            f"{self.sections} 节 · {self.chars} 字 · {self.images} 张图 · "
            f"覆盖 {self.time_coverage:.0%} · {state}"
        )


def evaluate_job(job_dir: Path) -> EvalReport:
    """读取一个任务目录，返回结构化评测结果。"""
    job_path = job_dir / "job.json"
    if not job_path.exists():
        raise Video2DocError(
            f"找不到任务文件：{job_path}",
            hint="指定包含 job.json 的任务目录。",
        )

    try:
        job = Job.model_validate_json(job_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Video2DocError(f"读不了 {job_path}：{exc}") from exc

    document = _load_stage(job_dir, job, "write", Document)
    if document is None:
        raise Video2DocError(
            "还没有 write 阶段的产物",
            hint="先运行 v2doc run，或换一个已经生成教程的任务目录。",
        )

    source = _load_stage(job_dir, job, "ingest", SourceInfo) or job.source
    transcript = _load_stage(job_dir, job, "correct", Transcript) or _load_stage(
        job_dir, job, "transcript", Transcript
    )
    ocr = _load_stage(job_dir, job, "ocr", OCRResult)
    critique = _load_stage(job_dir, job, "critique", Critique)

    warnings: list[str] = []
    notes: list[str] = []

    overrides = load_overrides(job_dir)
    if overrides is not None and not overrides.is_empty():
        document, override_warnings = apply_overrides(document, overrides)
        warnings.extend(override_warnings)
        notes.append("已应用 overrides.json，评测的是最终稿")

    duration = _duration(source, document, transcript)

    section_metrics: list[SectionMetric] = []
    image_paths: dict[str, int] = {}
    timestamp_errors = 0
    missing_image_refs = 0
    unused_images = 0
    empty_sections = 0
    short_sections = 0
    long_sections = 0
    code_blocks = 0

    for section in document.sections:
        chars = section.chars or count_chars(section.markdown)
        ids = [int(match) for match in IMG_RE.findall(section.markdown)]
        valid_ids = [image_id for image_id in ids if 1 <= image_id <= len(section.images)]
        missing = len(ids) - len(valid_ids)
        unused = len(section.images) - len(set(valid_ids))

        if section.start < 0 or section.end < section.start:
            timestamp_errors += 1
        elif duration and section.end > duration + 2:
            timestamp_errors += 1

        for image in section.images:
            if image.time < 0 or (duration and image.time > duration + 2):
                timestamp_errors += 1
            image_paths[image.image] = image_paths.get(image.image, 0) + 1

        if not section.markdown.strip() or chars == 0:
            empty_sections += 1
        elif chars < 80:
            short_sections += 1
        if chars > 2000:
            long_sections += 1

        code_blocks += section.markdown.count("```") // 2
        missing_image_refs += missing
        unused_images += unused
        section_metrics.append(
            SectionMetric(
                index=section.index,
                title=section.title,
                start=round(section.start, 3),
                end=round(section.end, 3),
                chars=chars,
                images=len(section.images),
                referenced=len(set(valid_ids)),
                missing_refs=missing,
                unused_images=unused,
            )
        )

    if transcript is not None:
        transcript_timestamp_errors = sum(
            1
            for utterance in transcript.utterances
            if utterance.start < 0
            or utterance.end < utterance.start
            or (duration and utterance.end > duration + 2)
        )
    else:
        transcript_timestamp_errors = 0

    time_coverage, gaps = _coverage(
        [(section.start, section.end) for section in document.sections], duration
    )

    html_path = job_dir / "tutorial.html"
    html_bytes = html_path.stat().st_size if html_path.exists() else 0
    html_text = (
        html_path.read_text(encoding="utf-8", errors="replace") if html_path.exists() else ""
    )
    figures = html_text.count("<figure")
    html_placeholders = html_text.count("[[img:")

    if html_bytes == 0:
        warnings.append("还没有 tutorial.html")
    if timestamp_errors:
        warnings.append(f"{timestamp_errors} 个章节/配图时间戳超出视频范围")
    if transcript_timestamp_errors:
        warnings.append(f"{transcript_timestamp_errors} 句转写的时间戳异常")
    if missing_image_refs:
        warnings.append(f"{missing_image_refs} 个 [[img:N]] 指向不存在的图")
    if html_placeholders:
        warnings.append(f"HTML 里还有 {html_placeholders} 个未替换的图片占位符")
    if empty_sections:
        warnings.append(f"{empty_sections} 节正文为空")
    if short_sections:
        warnings.append(f"{short_sections} 节正文少于 80 字")
    if long_sections:
        warnings.append(f"{long_sections} 节正文超过 2000 字")
    if unused_images:
        ratio = unused_images / document.images if document.images else 0.0
        if ratio >= 0.3:
            warnings.append(f"{unused_images} 张配图没有在正文引用（{ratio:.0%}）")
    no_image_sections = sum(
        1 for metric in section_metrics if metric.images and metric.referenced == 0
    )
    if no_image_sections:
        warnings.append(f"{no_image_sections} 节有配图但正文一张都没引用")
    if time_coverage and time_coverage < 0.85:
        warnings.append(f"章节只覆盖了 {time_coverage:.0%} 的视频时长")
    if duration and duration > 120 and not document.sections:
        warnings.append("没有生成任何章节")

    duplicate_images = sum(1 for count in image_paths.values() if count > 1)
    if duplicate_images:
        warnings.append(f"{duplicate_images} 张图被多个章节重复使用")

    ocr_frames = len(ocr.frames) if ocr else 0
    ocr_text_frames = (
        sum(1 for frame in ocr.frames if frame.char_count > 0 or frame.text.strip()) if ocr else 0
    )
    ocr_chars = ocr.total_chars if ocr else 0
    code_frames = sum(1 for frame in ocr.frames if looks_like_code(frame.text)) if ocr else 0

    if ocr is None:
        notes.append("没有 OCR 产物，跳过画面文字检查")
    elif code_frames >= 3 and code_blocks == 0:
        warnings.append(f"画面里有 {code_frames} 帧像代码，但正文没有代码块")
    if code_blocks > 0 and code_frames == 0:
        warnings.append(f"正文有 {code_blocks} 个代码块，但 OCR 没读到代码画面，注意核对")

    if transcript is None:
        notes.append("没有转写产物，跳过语音覆盖率检查")
    elif transcript.coverage < 0.5:
        warnings.append(f"转写只覆盖了 {transcript.coverage:.0%} 的语音时长")
    if critique is None:
        notes.append("没有质检产物，跳过 critique 检查")
        high = 0
    else:
        high = sum(1 for issue in critique.issues if issue.severity == "high")
        if high:
            warnings.append(f"质检有 {high} 条高优先级问题")
        elif critique.issues:
            notes.append(f"质检有 {len(critique.issues)} 条低优先级提示")

    total_elapsed = sum(record.elapsed for record in job.stages.values())
    stage_elapsed = {
        name: round(record.elapsed, 2) for name, record in job.stages.items() if record.elapsed > 0
    }
    chars = document.chars or sum(metric.chars for metric in section_metrics)
    minutes = duration / 60 if duration else 0.0
    chars_per_minute = round(chars / minutes, 1) if minutes else 0.0

    write_record = job.stages.get("write")
    if job.usage.calls == 0 and write_record is not None and write_record.elapsed > 0:
        notes.append("job.json 没有记录 LLM 用量（可能是旧版本任务）")

    return EvalReport(
        job_id=job.job_id,
        title=(source.title if source else "") or document.title,
        target=job.target,
        duration=round(duration, 3),
        sections=len(document.sections),
        chars=chars,
        images=document.images or sum(len(section.images) for section in document.sections),
        chars_per_minute=chars_per_minute,
        html_bytes=html_bytes,
        figures=figures,
        code_blocks=code_blocks,
        html_placeholders=html_placeholders,
        section_metrics=section_metrics,
        timestamp_errors=timestamp_errors,
        time_coverage=round(time_coverage, 4),
        gaps=gaps,
        empty_sections=empty_sections,
        short_sections=short_sections,
        long_sections=long_sections,
        missing_image_refs=missing_image_refs,
        unused_images=unused_images,
        duplicate_images=duplicate_images,
        ocr_frames=ocr_frames,
        ocr_text_frames=ocr_text_frames,
        ocr_chars=ocr_chars,
        code_frames=code_frames,
        transcript_utterances=len(transcript.utterances) if transcript else 0,
        transcript_chars=transcript.char_count if transcript else 0,
        transcript_coverage=round(transcript.coverage, 4) if transcript else 0.0,
        transcript_timestamp_errors=transcript_timestamp_errors,
        critique_issues=len(critique.issues) if critique else 0,
        critique_high=high,
        critique_coverage=round(critique.coverage, 4) if critique else 0.0,
        rewrites=critique.rewrites if critique else 0,
        llm_calls=job.usage.calls,
        llm_prompt_tokens=job.usage.prompt_tokens,
        llm_completion_tokens=job.usage.completion_tokens,
        total_elapsed=round(total_elapsed, 2),
        stage_elapsed=stage_elapsed,
        warnings=warnings,
        notes=notes,
        passed=not warnings,
    )


def _load_stage(job_dir: Path, job: Job, name: str, model: type[T]) -> T | None:
    record = job.stages.get(name)
    if record is None or record.status != "done" or not record.output:
        return None
    path = job_dir / record.output
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _duration(
    source: SourceInfo | None, document: Document, transcript: Transcript | None
) -> float:
    """优先用视频本身的时长；都没有时才用最后一节的结束时间兜底。"""
    for value in (
        source.duration if source else None,
        document.duration or None,
        transcript.duration if transcript else None,
    ):
        if value and value > 0:
            return value
    return max((section.end for section in document.sections), default=0.0)


def _coverage(
    intervals: list[tuple[float, float]], duration: float
) -> tuple[float, list[list[float]]]:
    if duration <= 0:
        return 0.0, []
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        start = max(0.0, start)
        end = min(duration, end)
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    gaps: list[list[float]] = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor > 1.0:
            gaps.append([round(cursor, 1), round(start, 1)])
        cursor = max(cursor, end)
    if duration - cursor > 1.0:
        gaps.append([round(cursor, 1), round(duration, 1)])

    covered = sum(end - start for start, end in merged)
    return min(1.0, covered / duration), gaps
