"""质检：先用确定性规则挑毛病，必要时再让 LLM 定向重写。

确定性规则能抓住的问题（太短、没配图、代码没围栏、覆盖率低）占大多数，
而且不花钱、结果稳定。LLM 只负责那些「确实写砸了」的段落。
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import WriteConfig
from ..errors import StageError
from ..schema import (
    Critique,
    Document,
    Issue,
    Segmentation,
    Transcript,
    VisualMoment,
)
from ..utils.common import hms
from ..utils.markdown import drop_leading_title
from .materials import assign_moments, build_material, count_chars, section_text
from .prompts import REWRITE_SYSTEM, rewrite_user
from .write import IMG_RE, sanitize_markdown

ProgressFn = Callable[[str], None]
CODE_CHARS = "{}();=<>"
MIN_SECTION_CHARS = 40
MIN_LENGTH_RATIO = 0.2
CODE_HINT = 8


def critique_document(
    document: Document,
    segmentation: Segmentation,
    transcript: Transcript,
    moments: list[VisualMoment],
    *,
    provider=None,
    config: WriteConfig | None = None,
    language: str = "zh",
    progress: ProgressFn | None = None,
) -> Critique:
    result = check(document, segmentation, transcript, moments)

    if provider is not None and config is not None and config.review and result.issues:
        rewrites = _rewrite(
            document,
            segmentation,
            transcript,
            moments,
            result.issues,
            provider,
            config,
            language=language,
            progress=progress,
        )
        if rewrites:
            result = check(document, segmentation, transcript, moments)
            result.rewrites = rewrites
    return result


def check(
    document: Document,
    segmentation: Segmentation,
    transcript: Transcript,
    moments: list[VisualMoment],
) -> Critique:
    issues: list[Issue] = []
    ocr_by_image = {moment.image: moment.ocr_text for moment in moments}
    segments = {section.index: section for section in segmentation.sections}
    used = 0

    for section in document.sections:
        segment = segments.get(section.index)
        material_chars = 0
        if segment is not None:
            material_chars = count_chars(
                section_text(transcript.utterances, segment.start_index, segment.end_index)
            )

        if section.chars < MIN_SECTION_CHARS:
            issues.append(
                Issue(
                    section=section.index,
                    code="empty",
                    severity="high",
                    message=f"「{section.title}」几乎没写出内容（{section.chars} 字）",
                )
            )
        elif material_chars >= 400 and section.chars / material_chars < MIN_LENGTH_RATIO:
            issues.append(
                Issue(
                    section=section.index,
                    code="too_short",
                    severity="medium",
                    message=(
                        f"「{section.title}」只有 {section.chars} 字，"
                        f"但原始转写有 {material_chars} 字，可能有信息被漏掉"
                    ),
                )
            )

        referenced = {int(match.group(1)) for match in IMG_RE.finditer(section.markdown)}
        valid = {ref.id for ref in section.images}
        used += len(referenced & valid)

        if section.images and not referenced:
            issues.append(
                Issue(
                    section=section.index,
                    code="no_image",
                    severity="medium",
                    message=(
                        f"「{section.title}」有 {len(section.images)} 张可用画面，"
                        "但正文没有引用"
                    ),
                )
            )
        if referenced - valid:
            issues.append(
                Issue(
                    section=section.index,
                    code="bad_image_ref",
                    severity="low",
                    message=f"「{section.title}」引用了不存在的图片编号",
                )
            )

        if _looks_like_code(section, ocr_by_image) and "```" not in section.markdown:
            issues.append(
                Issue(
                    section=section.index,
                    code="code_missing",
                    severity="medium",
                    message=f"「{section.title}」的画面里有代码，但正文没有代码块",
                )
            )

    duration = segmentation.duration or document.duration
    covered = sum(max(0.0, section.end - section.start) for section in document.sections)
    coverage = round(min(1.0, covered / duration), 4) if duration else 0.0
    if duration and coverage < 0.9:
        issues.append(
            Issue(
                section=0,
                code="coverage",
                severity="low",
                message=f"章节只覆盖了 {coverage:.0%} 的时间轴",
            )
        )

    available = len(moments)
    if available and used < available * 0.6:
        issues.append(
            Issue(
                section=0,
                code="images_unused",
                severity="low",
                message=f"只用了 {used}/{available} 张画面",
            )
        )

    return Critique(
        issues=issues,
        coverage=coverage,
        images_used=used,
        images_available=available,
        passed=not any(issue.severity == "high" for issue in issues),
    )


def _looks_like_code(section, ocr_by_image: dict[str, str]) -> bool:
    text = " ".join(ocr_by_image.get(ref.image, "") for ref in section.images)
    if not text:
        return False
    hits = sum(text.count(char) for char in CODE_CHARS)
    return hits >= CODE_HINT


def _rewrite(
    document: Document,
    segmentation: Segmentation,
    transcript: Transcript,
    moments: list[VisualMoment],
    issues: list[Issue],
    provider,
    config: WriteConfig,
    *,
    language: str,
    progress: ProgressFn | None,
) -> int:
    """只重写严重问题，且每节最多一次。重写质量不过关就保留原稿。"""
    targets: list[int] = []
    for issue in issues:
        if issue.severity == "high" and issue.section not in targets:
            targets.append(issue.section)
    targets = targets[: config.max_rewrites]

    buckets = assign_moments(segmentation.sections, moments)
    segments = {section.index: section for section in segmentation.sections}
    sections = {section.index: section for section in document.sections}
    rewritten = 0

    for index in targets:
        segment = segments.get(index)
        section = sections.get(index)
        if segment is None or section is None:
            continue
        if progress:
            progress(f"定向重写 {index} · {section.title}")
        material = build_material(
            segment,
            transcript.utterances,
            buckets.get(index, []),
            max_images=config.max_images_per_section,
            max_chars=config.max_section_chars,
        )
        related = [issue.message for issue in issues if issue.section == index]
        try:
            payload = provider.complete_json(
                REWRITE_SYSTEM,
                rewrite_user(
                    section_title=section.title,
                    issues=related,
                    images=material.prompt_images,
                    transcript=material.text,
                    markdown=section.markdown,
                    language=language,
                ),
                progress=progress,
            )
        except StageError:
            # 重写是加分项，接口挂了就保留原稿，别让已经写好的文档作废
            break
        markdown = str(payload.get("markdown") or payload.get("md") or "").strip()
        markdown = sanitize_markdown(markdown, len(material.refs))
        markdown = drop_leading_title(markdown, section.title)
        if count_chars(markdown) < min(section.chars * 0.6, section.chars + 50):
            continue
        section.markdown = markdown
        section.chars = count_chars(markdown)
        section.summary = str(payload.get("summary") or section.summary)[:120]
        section.rewritten = True
        rewritten += 1

    document.chars = sum(item.chars for item in document.sections)
    return rewritten


def describe(critique: Critique) -> str:
    if not critique.issues:
        return "质检通过"
    return "；".join(
        f"{hms(0) if issue.section == 0 else f'第 {issue.section} 节'}：{issue.message}"
        for issue in critique.issues[:5]
    )
