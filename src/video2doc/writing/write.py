"""逐节写作：把一节的转写 + 配图 OCR 喂给 LLM，拿回 Markdown。"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..config import WriteConfig
from ..schema import Document, ImageRef, Segmentation, Transcript, VisualMoment, WrittenSection
from ..utils.common import hms
from ..utils.markdown import demote_h1, drop_leading_title
from .materials import assign_moments, build_material, count_chars
from .prompts import INTRO_SYSTEM, WRITE_SYSTEM, intro_user, write_user

IMG_RE = re.compile(r"\[\[img:(\d+)\]\]", re.IGNORECASE)
ProgressFn = Callable[[str], None]
RETRY_SUFFIX = "\n\n注意：上一次没有返回 markdown 正文。这次必须返回合法 JSON，且 markdown 不为空。"


def write_document(
    segmentation: Segmentation,
    transcript: Transcript,
    moments: list[VisualMoment],
    provider,
    config: WriteConfig,
    *,
    title: str,
    language: str,
    progress: ProgressFn | None = None,
) -> Document:
    buckets = assign_moments(segmentation.sections, moments)
    sections: list[WrittenSection] = []

    for order, section in enumerate(segmentation.sections, 1):
        if progress:
            progress(f"写作 {order}/{len(segmentation.sections)} · {section.title}")
        material = build_material(
            section,
            transcript.utterances,
            buckets.get(section.index, []),
            max_images=config.max_images_per_section,
            max_chars=config.max_section_chars,
        )
        user = write_user(
            section_title=section.title,
            kind=section.kind,
            start=hms(section.start),
            end=hms(section.end),
            images=material.prompt_images,
            transcript=material.text,
            language=language,
        )

        payload = provider.complete_json(WRITE_SYSTEM, user, progress=progress)
        markdown = _payload_markdown(payload)
        if not markdown:
            payload = provider.complete_json(WRITE_SYSTEM, user + RETRY_SUFFIX, progress=progress)
            markdown = _payload_markdown(payload)

        if not markdown:
            # 宁可给一段可读的原始转写，也不要让整条流水线白跑
            markdown = _fallback_markdown(material.text, material.refs)

        markdown = sanitize_markdown(markdown, len(material.refs))
        markdown = drop_leading_title(markdown, section.title)
        sections.append(
            WrittenSection(
                index=section.index,
                title=section.title,
                kind=section.kind,
                start=section.start,
                end=section.end,
                markdown=markdown,
                summary=str(payload.get("summary") or "").strip()[:120],
                images=material.refs,
                chars=count_chars(markdown),
            )
        )

    intro = _write_intro(sections, provider, title, transcript, language, progress)
    images = sum(len(section.images) for section in sections)
    return Document(
        title=title,
        intro=intro,
        model=str(getattr(provider.config, "model", "")),
        language=language,
        duration=segmentation.duration or (transcript.duration or 0.0),
        sections=sections,
        chars=sum(section.chars for section in sections),
        images=images,
    )


def sanitize_markdown(markdown: str, image_count: int) -> str:
    text = str(markdown or "").strip()
    text = _unwrap_markdown_fence(text)
    text = demote_h1(text)
    # 占位符必须独占一行，渲染时才能变成合法的 <figure> 块
    text = re.sub(r"(?<!\n)(\[\[img:\d+\]\])", r"\n\n\1\n\n", text, flags=re.IGNORECASE)

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return match.group(0) if 1 <= index <= image_count else ""

    text = IMG_RE.sub(replace, text)
    if image_count and not IMG_RE.search(text):
        # 有图却没用：补在最后，至少保证图文教程里有图
        text = text.rstrip() + "\n\n[[img:1]]"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _unwrap_markdown_fence(text: str) -> str:
    """只剥掉模型偶尔加的 ```markdown 包装，别把真正的代码块拆了。"""
    first_line, _, rest = text.partition("\n")
    if first_line.strip().lower() not in {"```", "```markdown", "```md"}:
        return text
    if not rest.rstrip().endswith("```"):
        return text
    return rest.rstrip()[:-3].strip()


def _payload_markdown(payload: dict) -> str:
    for key in ("markdown", "md", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _fallback_markdown(text: str, refs: list[ImageRef]) -> str:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    body = "\n\n".join(paragraphs[:8]) or "（这一节没有可用的转写内容。）"
    if refs:
        body += "\n\n[[img:1]]"
    return body


def _write_intro(
    sections: list[WrittenSection],
    provider,
    title: str,
    transcript: Transcript,
    language: str,
    progress: ProgressFn | None,
) -> str:
    if not sections:
        return ""
    try:
        payload = provider.complete_json(
            INTRO_SYSTEM,
            intro_user(
                title=title,
                duration=hms(transcript.duration),
                sections=[
                    (section.title, section.summary or section.title) for section in sections
                ],
                language=language,
            ),
            progress=progress,
        )
    except Exception:  # noqa: BLE001 - 导语是锦上添花，失败不该毁掉整篇
        return ""
    return str(payload.get("intro") or "").strip()[:300]
