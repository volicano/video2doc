"""从视频元数据里自动生成 ASR 热词。

这是性价比最高的一步：标题和标签本来就是这条视频的主题词，
把它们塞进 whisper 的 initial_prompt，中文术语识别率能明显改善，
而成本几乎为零。
"""

from __future__ import annotations

import re

from .schema import SourceInfo

_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{1,19}")
_SPLIT_RE = re.compile(r"[\s,，。、/|:：\-—_【】\[\]()（）!?？!·]+")

# 标题里的通用词，对识别没有帮助，反而会挤占 prompt 空间
STOPWORDS = {
    "视频",
    "教程",
    "课程",
    "全集",
    "合集",
    "完整版",
    "高清",
    "公开课",
    "分享",
    "讲解",
    "面试题",
    "程序员",
    "程序员面试",
    "一集",
    "上集",
    "下集",
    "第一期",
    "第二期",
    "免费",
    "入门",
    "进阶",
    "实战",
    "干货",
}

MAX_HOTWORDS = 24


def auto_hotwords(source: SourceInfo, limit: int = MAX_HOTWORDS) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    def push(word: str) -> None:
        word = word.strip(" .-·")
        if not word or len(word) < 2 or word.isdigit():
            return
        if _is_generic(word):
            return
        key = word.lower()
        if key in seen:
            return
        seen.add(key)
        result.append(word)

    for tag in source.tags:
        for piece in _SPLIT_RE.split(tag):
            if piece:
                push(piece)

    haystack = " ".join(filter(None, [source.title, source.description or ""]))
    if source.kind == "url":
        # 本地文件的 title 是文件名，里面的英文标识符多半是 "asr-demo" 这种噪声
        for match in _LATIN_RE.findall(haystack):
            push(match)
    for match in _CJK_RE.findall(source.title):
        push(match)

    return result[:limit]


def _is_generic(word: str) -> bool:
    """中文没有天然分词边界，`视频教程` 这种整块也应该整块丢掉。"""
    lowered = word.lower()
    if lowered in STOPWORDS:
        return True
    return any(lowered.startswith(stop) for stop in STOPWORDS if len(stop) >= 2)


def resolve_hotwords(
    config_hotwords: list[str], auto_enabled: bool, source: SourceInfo
) -> list[str]:
    """用户手写的排前面，自动生成的补后面。"""
    words: list[str] = []
    seen: set[str] = set()
    for word in list(config_hotwords) + (auto_hotwords(source) if auto_enabled else []):
        key = word.strip().lower()
        if key and key not in seen:
            seen.add(key)
            words.append(word.strip())
    return words[:MAX_HOTWORDS]


def build_initial_prompt(
    hotwords: list[str], language: str = "zh", extra: str | None = None
) -> str | None:
    parts: list[str] = []
    if language.startswith("zh"):
        parts.append("以下是一段中文技术教学视频的讲解，可能涉及这些术语：")
        if hotwords:
            parts.append("、".join(hotwords))
            parts.append("。")
    else:
        parts.append("Technical tutorial narration.")
        if hotwords:
            parts.append("Terms: " + ", ".join(hotwords) + ".")
    if extra:
        parts.append(extra.strip())
    prompt = "".join(parts).strip()
    return prompt or None
