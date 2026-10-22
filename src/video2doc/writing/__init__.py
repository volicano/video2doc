"""写作层：分段 → 逐节写作 → 质检。

对外只暴露几个入口，阶段文件只做「读上游产物 / 调这里 / 写产物」。
"""

from __future__ import annotations

import os
import re

from ..config import Config
from ..pipeline import Context
from ..providers.llm import LLMProvider, create_llm_from_config
from ..schema import Transcript

__all__ = [
    "detect_language",
    "llm_unavailable",
    "load_transcript",
    "make_llm",
    "resolve_language",
]

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def load_transcript(ctx: Context) -> Transcript:
    """优先用校正后的转写。句子数量不变，所以分段边界对两份转写都成立。"""
    record = ctx.job.stages.get("correct")
    if record is not None and record.status == "done" and record.output:
        return ctx.load("correct", Transcript)
    return ctx.load("transcript", Transcript)


def make_llm(config: Config, temperature: float | None = None) -> LLMProvider:
    """写作和校对对创造性的要求不同，所以温度按阶段覆盖。"""
    llm_config = config.llm
    if temperature is not None:
        llm_config = llm_config.model_copy(update={"temperature": temperature})
    return create_llm_from_config(llm_config)


def detect_language(text: str, *, fallback: str = "zh") -> str:
    sample = text[:4000]
    cjk = len(_CJK_RE.findall(sample))
    latin = len(_LATIN_RE.findall(sample))
    if cjk == 0 and latin < 20:
        return fallback
    return "zh" if cjk >= latin / 3 else "en"


def resolve_language(config: Config, transcript: Transcript) -> str:
    if config.write.language in ("zh", "en"):
        return config.write.language
    sample = "".join(utterance.text for utterance in transcript.utterances[:80])
    return detect_language(sample)


def llm_unavailable(config: Config) -> str | None:
    """没配密钥时不要在中途炸掉整条流水线，跳过写作并在总结里说明。"""
    env_name = config.llm.api_key_env
    if env_name and not os.environ.get(env_name):
        return f"未设置 {env_name}，跳过写作"
    return None
