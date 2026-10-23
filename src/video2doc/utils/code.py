"""代码画面的识别、抽取与排版。

OCR 的输出是「行」而不是「段」。普通画面可以压成一行当图注，
代码画面一旦压平，缩进和换行就没了，模型只能把它改写成散文。
这里用一个保守的启发式把两者分开，并尽量从嘈杂的 OCR 里捞出代码。
"""

from __future__ import annotations

import re

# 只认真正的代码行首关键字，不把界面文案里的 function / if 当代码
_STRONG_RE = re.compile(
    r"^\s*(?:"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|def|class)\s+[A-Za-z_$]"
    r"|(?:const|let|var)\s+[A-Za-z_$]"
    r"|(?:import|from)\s+[A-Za-z_{*'\"]"
    r"|#include\s*[<\"]"
    r"|(?:public|private|protected|static|void|int|float|double|bool|char)\s+[A-Za-z_$]"
    r"|(?:npm|npx|pip3?|git|curl|wget|node|python3?|bash|sh|powershell)\s+\S"
    r"|\$\s+\S"
    r")"
)
# 流程关键字单独匹配，并要求同一行有括号/分号，避免 "if you want" 这种文案
_FLOW_RE = re.compile(
    r"^\s*(?:return|await|async|yield|throw|if|elif|else|for|while|switch|case|try|catch|finally)\b"
)
_OP_RE = re.compile(r"(?:=>|===|!==|::|&&|\|\||\+=|-=|->|:=)")
_ASSIGN_RE = re.compile(r"(?<![=!<>])=(?!=|>)")
_PUNCT_ONLY_RE = re.compile(r"^[{}();=]+$")
_CALL_RE = re.compile(r"^[a-z_$][\w$.]*\s*\(.*\)\s*[;,]?$")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_STRUCT_RE = re.compile(r"[{}();=]")


def looks_like_code(text: str) -> bool:
    """判断一帧 OCR 是否像代码画面。

    只靠括号/分号数量会把 UI 文案和普通笔记误判；这里优先看行首关键字、
    heredoc，以及足够多「像代码」的独立行。宁可漏掉一两张截图，也不要把
    OCR 噪声塞进正文。
    """
    if not text:
        return False
    if _HEREDOC_RE.search(text):
        return True

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    strong = [line for line in lines if _strong_code_line(line)]
    if strong:
        return True

    # 没有关键字时，只接受整帧由多行代码/JSON 组成的画面
    codeish = [line for line in lines if _code_line(line)]
    return len(codeish) >= 3 and len(codeish) >= len(lines) / 2


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


def _strong_code_line(line: str) -> bool:
    text = line.strip()
    if not text or len(text) <= 2:
        return False
    if _CJK_RE.search(text) and not re.search(r"[=;{}]", text):
        return False
    if _STRONG_RE.search(text):
        return True
    if _FLOW_RE.search(text):
        return bool(_STRUCT_RE.search(text) or _OP_RE.search(text))
    if _OP_RE.search(text):
        return bool(re.search(r"[{}();]", text))
    return False


def _code_line(line: str) -> bool:
    text = line.strip()
    if not text or len(text) <= 2:
        return False
    if _strong_code_line(text):
        return True
    # 中文行只保留明确带赋值/分号/大括号的代码；普通中文说明和模板语法丢掉
    if _CJK_RE.search(text):
        return False
    if _PUNCT_ONLY_RE.fullmatch(text):
        return bool(re.search(r"[{};=]", text))
    if _CALL_RE.fullmatch(text):
        return True
    if _OP_RE.search(text) and re.search(r"[{}();]", text):
        return True
    # 代码块尾部：timeout:20}) / }; / },
    if "}" in text and re.search(r"[})][）)]?\s*$", text):
        return True
    return bool((_ASSIGN_RE.search(text) or ";" in text) and re.search(r"[A-Za-z0-9_$]", text))


def _guess_language(code: str) -> str:
    if re.search(r"\b(?:const|let|var|function|await|async)\b|=>", code):
        return "js"
    if re.search(r"^\s*(?:def|class|import|from)\b", code, re.MULTILINE):
        return "python"
    if re.search(r"^\s*(?:#include|int|void)\b", code, re.MULTILINE):
        return "c"
    return "text"
