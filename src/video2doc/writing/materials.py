"""把「一节」需要喂给模型的东西准备好。

写作和质检重写都要用同一套材料，所以抽出来，保证两边看到的输入完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import ImageRef, Section, Utterance, VisualMoment
from ..utils.common import hms

CAPTION_CHARS = 40
PROMPT_OCR_CHARS = 400


@dataclass
class Material:
    text: str
    refs: list[ImageRef] = field(default_factory=list)
    prompt_images: list[str] = field(default_factory=list)


def section_text(
    utterances: list[Utterance],
    start_index: int,
    end_index: int,
    *,
    max_chars: int = 6000,
) -> str:
    slice_ = utterances[max(0, start_index) : max(0, end_index)]
    text = "\n".join(utterance.text.strip() for utterance in slice_ if utterance.text.strip())
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n……（后文过长，已截断）"
    return text


def assign_moments(
    sections: list[Section], moments: list[VisualMoment]
) -> dict[int, list[VisualMoment]]:
    """每张图只归给与它重叠最多的那一节，避免同一张截图在全文出现两次。"""
    buckets: dict[int, list[VisualMoment]] = {section.index: [] for section in sections}
    for moment in moments:
        best_index: int | None = None
        best_overlap = 0.0
        for section in sections:
            overlap = min(section.end, moment.end) - max(section.start, moment.start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_index = section.index
        if best_index is not None:
            buckets[best_index].append(moment)
    return buckets


def build_material(
    section: Section,
    utterances: list[Utterance],
    moments: list[VisualMoment],
    *,
    max_images: int = 4,
    max_chars: int = 6000,
) -> Material:
    text = section_text(utterances, section.start_index, section.end_index, max_chars=max_chars)
    chosen = _select_moments(moments, max_images)
    refs = [
        ImageRef(
            id=index,
            time=moment.time,
            image=moment.image,
            kind=moment.kind,
            caption=caption_for(moment),
        )
        for index, moment in enumerate(chosen, 1)
    ]
    prompt_images = [
        f"[{ref.id}] {hms(ref.time)} · {ref.kind}"
        + (f" · 画面文字：{image_digest(moment)}" if image_digest(moment) else "")
        for ref, moment in zip(refs, chosen, strict=False)
    ]
    return Material(text=text, refs=refs, prompt_images=prompt_images)


def _select_moments(moments: list[VisualMoment], limit: int) -> list[VisualMoment]:
    if limit <= 0 or not moments:
        return []
    # 先按信息量取前 N 张，再按时间排序，保证编号顺序和讲述顺序一致
    best = sorted(moments, key=lambda moment: moment.score, reverse=True)[:limit]
    best.sort(key=lambda moment: moment.time)
    return best


def caption_for(moment: VisualMoment) -> str:
    """图注优先用 OCR 的第一行，它通常就是这页 PPT 的标题。"""
    for line in moment.ocr_text.splitlines():
        cleaned = line.strip()
        if len(cleaned) >= 2:
            return cleaned[:CAPTION_CHARS]
    return ""


def image_digest(moment: VisualMoment) -> str:
    text = " ".join(moment.ocr_text.split())
    return text[:PROMPT_OCR_CHARS]


def count_chars(markdown: str) -> int:
    """去掉空白和 Markdown 符号后的近似字数，用来判断写得够不够。"""
    stripped = markdown
    for token in ("```", "##", "#", "-", "*", ">", "|", "`"):
        stripped = stripped.replace(token, "")
    return len("".join(stripped.split()))
