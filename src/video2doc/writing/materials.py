"""把「一节」需要喂给模型的东西准备好。

写作和质检重写都要用同一套材料，所以抽出来，保证两边看到的输入完全一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schema import ImageRef, Section, Utterance, VisualMoment
from ..utils.code import extract_code_block, looks_like_code
from ..utils.common import hms

CAPTION_CHARS = 40
PROMPT_OCR_CHARS = 400
PROMPT_CODE_CHARS = 1400
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
    code_blocks: list[str] = field(default_factory=list)


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
    prompt_images = [_prompt_image(ref, moment) for ref, moment in zip(refs, chosen, strict=False)]
    code_blocks = [
        block
        for moment in chosen
        if _is_code_moment(moment) and (block := extract_code_block(moment.ocr_text))
    ]
    return Material(
        text=text,
        refs=refs,
        prompt_images=prompt_images,
        code_blocks=code_blocks,
    )


def _prompt_image(ref: ImageRef, moment: VisualMoment) -> str:
    head = f"[{ref.id}] {hms(ref.time)} · {ref.kind}"
    digest = image_digest(moment)
    if not digest:
        return head
    if digest.startswith("【代码画面】"):
        # 代码单独起一行，模型才看得见换行和缩进
        return f"{head}\n{digest}"
    return f"{head} · 画面文字：{digest}"


def _select_moments(moments: list[VisualMoment], limit: int) -> list[VisualMoment]:
    if limit <= 0 or not moments:
        return []
    ranked = sorted(moments, key=lambda moment: moment.score, reverse=True)

    # 代码画面通常在「信息密度」上打不过整屏 UI，但它是正文里唯一能还原代码的依据。
    # 给代码画面保留一半名额，避免它们全部被高分截图挤掉。
    selected: list[VisualMoment] = []
    code_moments = [moment for moment in ranked if _is_code_moment(moment)]
    if code_moments:
        selected.extend(code_moments[: max(1, limit // 2)])

    for moment in ranked:
        if len(selected) >= limit:
            break
        if moment not in selected:
            selected.append(moment)

    selected = selected[:limit]
    # 编号顺序跟着讲述顺序走
    selected.sort(key=lambda moment: moment.time)
    return selected


def _is_code_moment(moment: VisualMoment) -> bool:
    return moment.kind == "code" or looks_like_code(moment.ocr_text)


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
    """给模型看的画面文字。

    普通画面压成一行，省 token；代码画面保留换行和缩进，否则模型没法还原代码。
    """
    text = moment.ocr_text.strip()
    if not text:
        return ""
    if looks_like_code(text):
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        body = "\n".join(lines)
        if len(body) > PROMPT_CODE_CHARS:
            body = body[:PROMPT_CODE_CHARS] + "\n……（后文过长，已截断）"
        return "【代码画面】\n" + body
    return " ".join(text.split())[:PROMPT_OCR_CHARS]


def count_chars(markdown: str) -> int:
    """去掉空白和 Markdown 符号后的近似字数，用来判断写得够不够。"""
    stripped = markdown
    for token in ("```", "##", "#", "-", "*", ">", "|", "`"):
        stripped = stripped.replace(token, "")
    return len("".join(stripped.split()))
