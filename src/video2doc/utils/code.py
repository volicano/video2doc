"""代码画面的识别、抽取与排版。

OCR 的输出是「行」而不是「段」。普通画面可以压成一行当图注，
代码画面一旦压平，缩进和换行就没了，模型只能把它改写成散文。
这里用一个保守的启发式把两者分开，并尽量从嘈杂的 OCR 里捞出代码。
"""

from __future__ import annotations

import re

# 只抓比较明确的代码特征，避免把带括号的界面文案当成代码
CODE_HINT_RE = re.compile(
    r"(=>|::|#include|</?[a-zA-Z][\w-]*>|\b(?:def|function|const|let|var|class|import|return|"
    r"async|await|public|private|static|void)\s+|[{}();]{3,}|\w+\([^)]*\)\s*;)"
)
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CODE_LINE_RE = re.compile(
    r"[=(){};]|\b(?:const|let|var|function|def|class|return|await|async|import|from|"
    r"if|else|for|while|cliLog|useOrCreate|openOrReuse)\b"
)


def looks_like_code(text: str) -> bool:
    return bool(text and CODE_HINT_RE.search(text))


def extract_code_block(text: str) -> str:
    """从一帧 OCR 里尽量抽出一段代码；抽不出就返回空串。

    优先找 heredoc（代码教程里很常见）：``bash <<'EOF' ... EOF``。
    OCR 会把侧边栏文字混进代码行，所以只保留带代码特征的行走。
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    heredocs: list[str] = []
    for index, line in enumerate(lines):
        match = _HEREDOC_RE.search(line)
        if match is None:
            continue
        delimiter = match.group(1)
        body: list[str] = []
        for inner in lines[index + 1 :]:
            if inner.strip().upper() == delimiter.upper():
                break
            if _code_line(inner):
                body.append(inner.strip())
        if body:
            heredocs.append("\n".join(body))
    if heredocs:
        return max(heredocs, key=len)

    # 没有 heredoc 时，退化成「挑出带代码符号的行」
    body = [line.strip() for line in lines if _code_line(line)]
    if len(body) < 2:
        return ""
    return "\n".join(body)


def code_block_markdown(code: str) -> str:
    language = _guess_language(code)
    return f"```{language}\n{code.strip()}\n```"


def _code_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if _CJK_RE.search(text) and not re.search(r"[=(){};]", text):
        return False
    if len(text) <= 2:
        return False
    return bool(_CODE_LINE_RE.search(text))


def _guess_language(code: str) -> str:
    if re.search(r"\b(?:const|let|var|function|await|async)\b|=>", code):
        return "js"
    if re.search(r"^\s*(?:def|class|import|from)\b", code, re.MULTILINE):
        return "python"
    if re.search(r"^\s*(?:#include|int|void)\b", code, re.MULTILINE):
        return "c"
    return "text"
