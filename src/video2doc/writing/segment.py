"""语义分段：把转写切成有标题的章节。

LLM 只负责回答「从第几句开始新的一章、这一章叫什么」，
边界校验、时长控制、过长再切、过多合并全部由代码完成。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from ..config import WriteConfig
from ..errors import StageError
from ..schema import Section, Segmentation, Transcript, Utterance
from ..utils.common import hms
from .prompts import SEGMENT_SYSTEM, language_directive, segment_user

KINDS = {"concept", "example", "qa", "summary", "setup", "other"}
ProgressFn = Callable[[str], None]


@dataclass
class _Entry:
    start_index: int
    end_index: int
    start: float
    end: float
    title: str
    kind: str = "concept"


def plan_chunks(total: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """把长转写切成带重叠的窗口；重叠是为了不漏掉跨窗口的主题切换。"""
    if total <= 0:
        return []
    if size <= 0:
        return [(0, total)]
    if total <= size:
        return [(0, total)]

    step = max(1, size - max(0, overlap))
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < total:
        end = min(total, start + size)
        chunks.append((start, end))
        if end >= total:
            break
        start += step
    return chunks


def segment_transcript(
    transcript: Transcript,
    provider,
    config: WriteConfig,
    *,
    title: str,
    language: str,
    progress: ProgressFn | None = None,
) -> Segmentation:
    utterances = transcript.utterances
    if not utterances:
        raise StageError("转写是空的，无法分段")

    duration = transcript.duration or utterances[-1].end
    chunks = plan_chunks(len(utterances), config.chunk_utterances, config.chunk_overlap)
    boundaries: dict[int, tuple[str, str]] = {}
    directive = language_directive(language)

    for order, (start, end) in enumerate(chunks, 1):
        if progress:
            progress(f"语义分段 {order}/{len(chunks)}…")
        lines = [
            f"[{index}] {hms(utterances[index].start)} {utterances[index].text.strip()}"
            for index in range(start, end)
        ]
        payload = provider.complete_json(
            SEGMENT_SYSTEM,
            segment_user(
                title=title,
                duration=hms(duration),
                start=start,
                end=end,
                lines=lines,
            )
            + f"\n\n{directive}",
            progress=progress,
        )
        for item in payload.get("sections") or []:
            index = _to_int(item.get("at")) if isinstance(item, dict) else None
            if index is None or not start <= index < end:
                continue
            name = _clean_title(item.get("title"))
            if not name or index in boundaries:
                continue
            boundaries[index] = (name, _clean_kind(item.get("kind")))

    if 0 not in boundaries:
        boundaries[0] = (_fallback_title(utterances[0].text), "concept")

    entries = _compose(boundaries, utterances, duration, config)
    entries = _split_long(entries, utterances, config.max_section_sec)
    entries = _merge_to_count(entries, config.max_sections)

    sections = [
        Section(
            index=index,
            title=entry.title or f"第 {index} 节",
            kind=entry.kind,
            start=round(entry.start, 3),
            end=round(entry.end, 3),
            start_index=entry.start_index,
            end_index=entry.end_index,
            utterances=entry.end_index - entry.start_index,
        )
        for index, entry in enumerate(entries, 1)
    ]
    return Segmentation(
        strategy="llm" if boundaries else "fallback",
        model="",
        chunks=len(chunks),
        duration=round(duration, 3),
        sections=sections,
    )


# ------------------------------------------------------------------ 归一化


def _compose(
    boundaries: dict[int, tuple[str, str]],
    utterances: list[Utterance],
    duration: float,
    config: WriteConfig,
) -> list[_Entry]:
    points = sorted(index for index in boundaries if 0 <= index < len(utterances))
    if not points:
        points = [0]

    kept = [points[0]]
    for point in points[1:]:
        if utterances[point].start - utterances[kept[-1]].start >= config.min_section_sec:
            kept.append(point)

    entries: list[_Entry] = []
    for position, start_index in enumerate(kept):
        end_index = kept[position + 1] if position + 1 < len(kept) else len(utterances)
        start = 0.0 if position == 0 else utterances[start_index].start
        end = utterances[end_index].start if end_index < len(utterances) else duration
        title, kind = boundaries.get(start_index, ("", "concept"))
        entries.append(
            _Entry(
                start_index=start_index,
                end_index=end_index,
                start=start,
                end=end,
                title=title,
                kind=kind,
            )
        )
    return entries


def _split_long(entries: list[_Entry], utterances: list[Utterance], max_sec: float) -> list[_Entry]:
    """单节太长时，在中间附近找一个停顿最大的位置补一刀。"""
    if max_sec <= 0:
        return entries

    result: list[_Entry] = []
    queue = list(entries)
    guard = 0
    while queue and guard < 1000:
        guard += 1
        entry = queue.pop(0)
        if entry.end - entry.start <= max_sec or entry.end_index - entry.start_index <= 8:
            result.append(entry)
            continue
        split = _best_split(entry, utterances)
        if split is None:
            result.append(entry)
            continue
        left = replace(entry, end_index=split, end=utterances[split].start)
        right = replace(
            entry,
            start_index=split,
            start=utterances[split].start,
            title=_continuation(entry.title),
        )
        queue.insert(0, right)
        queue.insert(0, left)

    result.extend(queue)
    return result


def _best_split(entry: _Entry, utterances: list[Utterance]) -> int | None:
    low = entry.start_index + 3
    high = entry.end_index - 2
    if low >= high:
        return None

    middle = (entry.start + entry.end) / 2
    window = max(5.0, (entry.end - entry.start) * 0.25)

    best: tuple[float, float, int] | None = None
    for index in range(low, high + 1):
        pause = utterances[index].start - utterances[index - 1].end
        distance = abs(utterances[index].start - middle)
        score = (1 if pause >= 0.3 else 0, -distance)
        if distance > window:
            continue
        if best is None or score > (best[0], best[1]):
            best = (score[0], score[1], index)

    if best is not None:
        return best[2]

    # 没有明显停顿就选离中点最近的一句
    candidates = range(low, high + 1)
    return min(candidates, key=lambda index: abs(utterances[index].start - middle), default=None)


def _merge_to_count(entries: list[_Entry], limit: int) -> list[_Entry]:
    if limit <= 0:
        return entries
    while len(entries) > limit and len(entries) > 1:
        index = min(
            range(len(entries) - 1),
            key=lambda position: entries[position + 1].end - entries[position].start,
        )
        left, right = entries[index], entries[index + 1]
        entries[index : index + 2] = [
            replace(left, end_index=right.end_index, end=right.end)
        ]
    return entries


def _continuation(title: str) -> str:
    return f"{title}（续）" if title else ""


def _fallback_title(text: str) -> str:
    cleaned = "".join(ch for ch in text.strip() if ch not in "，。！？、,.!?;；:：")
    return cleaned[:16] or "开场"


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_title(value: Any) -> str:
    text = str(value or "").strip().strip("\"'「」《》")
    text = " ".join(text.split())
    return text[:30]


def _clean_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in KINDS else "concept"
