"""字幕解析：VTT / SRT -> 带时间戳的句子列表。

为什么值得单独写而不是装个库：B站和 YouTube 的字幕各有各的脏数据
（滚动重复行、`<v Speaker>`、cue 设置、GBK 编码），自己处理可控得多。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from .schema import SubtitleFile, Utterance

_TAG_RE = re.compile(r"<[^>]*>")
_ASS_RE = re.compile(r"\{\\[^}]*\}")
_SPEAKER_RE = re.compile(r"<v(?:\s+([^>]+))?>")
_WS_RE = re.compile(r"\s+")

_NOISE_PATTERNS = (
    "[音乐]",
    "[掌声]",
    "[笑声]",
    "[applause]",
    "[music]",
    "[laughter]",
    "[silence]",
    "[欢快音乐]",
)

DEFAULT_SUBTITLE_LANGS = ("zh-Hans", "zh-CN", "zh", "zh-Hant", "en")


def read_subtitle_text(path: Path) -> str:
    """字幕文件编码五花八门，按常见顺序试。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_timestamp(text: str) -> float:
    """支持 `00:01:02.500`、`01:02.500`、`01:02,500`。失败返回 -1。"""
    raw = text.strip().replace(",", ".")
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = "0", parts[0], parts[1]
        else:
            hours, minutes, seconds = "0", "0", parts[0]
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, IndexError):
        return -1.0


def parse_subtitle_text(
    text: str,
    *,
    merge_gap: float = 0.0,
    drop_noise: bool = True,
    max_merge_chars: int = 80,
) -> list[Utterance]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"\n\s*\n", text)

    cues: list[Utterance] = []
    previous_lines: list[str] = []

    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue  # WEBVTT 头、NOTE、STYLE 之类

        clock = lines[timing_index]
        left, _, right = clock.partition("-->")
        start = parse_timestamp(left)
        end = parse_timestamp(right.strip().split(" ")[0] if right.strip() else "")
        if start < 0 or end <= start:
            continue

        cleaned: list[str] = []
        speakers: set[str] = set()
        for line in lines[timing_index + 1 :]:
            for speaker in _SPEAKER_RE.findall(line):
                speakers.add(speaker.strip())
            piece = _clean_line(line)
            if not piece:
                continue
            # YouTube 自动字幕会把上一句再带一遍，去掉滚动重复
            if piece in previous_lines:
                continue
            cleaned.append(piece)

        if not cleaned:
            previous_lines = previous_lines or []
            continue
        previous_lines = cleaned

        body = " ".join(cleaned)
        if drop_noise and _is_noise(body):
            continue
        cues.append(
            Utterance(
                start=round(start, 3),
                end=round(end, 3),
                text=body,
                speaker=next(iter(speakers)) if len(speakers) == 1 else None,
            )
        )

    return _merge(cues, merge_gap, max_merge_chars)


def parse_subtitle_file(path: Path, *, merge_gap: float = 0.0, **kwargs) -> list[Utterance]:
    """人工字幕通常已经是完整的句子，不做合并；自动字幕才需要把碎片拼起来。"""
    return parse_subtitle_text(read_subtitle_text(path), merge_gap=merge_gap, **kwargs)


def pick_subtitle(subs: list[SubtitleFile], preferred: str = "zh") -> SubtitleFile | None:
    """人工字幕优先，其次语言匹配度。"""
    if not subs:
        return None
    base = preferred.lower().split("-")[0]

    def score(sub: SubtitleFile) -> int:
        lang = sub.language.lower()
        value = 0
        if lang == preferred.lower():
            value = 5
        elif lang.split("-")[0] == base:
            value = 4
        elif lang.startswith("zh"):
            value = 3
        elif lang.startswith("en"):
            value = 1
        if value == 0:
            # 语言完全不匹配时宁可用 ASR，也不要拿一份看不懂的字幕凑数
            return 0
        return value + 3 if not sub.auto else value

    best = max(subs, key=score)
    return best if score(best) > 0 else None


def _clean_line(line: str) -> str:
    line = _SPEAKER_RE.sub("", line)
    line = _TAG_RE.sub("", line)
    line = _ASS_RE.sub("", line)
    line = html.unescape(line)
    line = line.replace("\u200b", "").replace("\ufeff", "")
    return _WS_RE.sub(" ", line).strip()


def _is_noise(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in _NOISE_PATTERNS


def _merge(cues: list[Utterance], gap: float, max_chars: int) -> list[Utterance]:
    if not cues:
        return []
    merged: list[Utterance] = [cues[0].model_copy()]
    for cue in cues[1:]:
        last = merged[-1]
        same_speaker = last.speaker == cue.speaker
        close_enough = cue.start - last.end < gap
        short_enough = len(last.text) + len(cue.text) <= max_chars
        if same_speaker and close_enough and short_enough:
            last.text = f"{last.text} {cue.text}".strip()
            last.end = max(last.end, cue.end)
        else:
            merged.append(cue.model_copy())
    return merged
