"""把「一节」需要喂给模型的东西准备好。

写作和质检重写都要用同一套材料，所以抽出来，保证两边看到的输入完全一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schema import ImageRef, Section, Utterance, VisualMoment
from ..utils.common import hms

CAPTION_CHARS = 40
PROMPT_OCR_CHARS = 400
# 这些多半是浏览器 / 应用界面的残留文字，当图注只会显得很潦草
_CAPTION_NOISE = {
    "bilibili",
    "youtube",
    "raw",
    "tags",
    "tag",
    "chrome",
    "edge",
    "firefox",
    "new tab",
    "untitled",
    "markdown",
    "无标题",
    "新标签页",
    "标记",
}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


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
    """图注取 OCR 里最像「这页标题」的一行，过滤掉浏览器标签页之类的噪音。"""
    lines = [" ".join(line.split()) for line in moment.ocr_text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    best = max(lines, key=_caption_score)
    if _caption_score(best) <= 0:
        return ""
    return best[:CAPTION_CHARS]


def _caption_score(text: str) -> float:
    if len(text) < 3:
        return -1.0
    if text.lower().strip(" .-_/|:：") in _CAPTION_NOISE:
        return -1.0
    if text.isdigit():
        return -1.0
    score = min(len(text), CAPTION_CHARS) / 10.0
    if _CJK_RE.search(text):
        score += 1.0
    elif text.islower():
        # 「calen」「bilibili」这种全小写英文基本是界面残留
        score -= 1.5
    return score


def image_digest(moment: VisualMoment) -> str:
    text = " ".join(moment.ocr_text.split())
    return text[:PROMPT_OCR_CHARS]


def count_chars(markdown: str) -> int:
    """去掉空白和 Markdown 符号后的近似字数，用来判断写得够不够。"""
    stripped = markdown
    for token in ("```", "##", "#", "-", "*", ">", "|", "`"):
        stripped = stripped.replace(token, "")
    return len("".join(stripped.split()))
