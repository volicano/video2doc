"""Markdown 文本的小工具。

只处理「标题层级」这一类结构问题，真正的解析交给 markdown-it。
注意所有变换都必须跳过代码块：Python 里的 `# 注释` 不是标题。
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _is_fence(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def map_outside_fences(text: str, transform) -> str:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _is_fence(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        lines.append(line if in_fence else transform(line))
    return "\n".join(lines)


def shift_headings(text: str, by: int = 1) -> str:
    """把正文标题整体降级，避免和页面自身的 h1/h2 打架。"""

    def transform(line: str) -> str:
        match = _HEADING_RE.match(line)
        if not match:
            return line
        level = min(6, max(1, len(match.group(1)) + by))
        return f"{'#' * level} {match.group(2)}"

    return map_outside_fences(text, transform)


def demote_h1(text: str) -> str:
    def transform(line: str) -> str:
        match = _HEADING_RE.match(line)
        if not match or len(match.group(1)) != 1:
            return line
        return f"## {match.group(2)}"

    return map_outside_fences(text, transform)


_PUNCT_RE = re.compile(r"[\s，。！？、,.!?;；:：「」『』\"'（）()\-—]+")


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", text)


def drop_leading_title(text: str, title: str) -> str:
    """模型经常把章节标题原样再写一遍，删掉开头那个重复标题。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        match = _HEADING_RE.match(line)
        if match and _norm(match.group(2)) == _norm(title):
            del lines[index]
            return "\n".join(lines).strip()
        return text
    return text
