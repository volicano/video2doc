"""correct 阶段：用 LLM 修术语和错别字。

只改 text，不碰 start/end —— 时间戳是全流程的锚点，绝不允许模型输出。
默认关闭：开启意味着把字幕文本发到 LLM 服务商。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..providers.llm import create_llm_from_config
from ..schema import Transcript, Utterance

SYSTEM_PROMPT = """\
你是一名中文技术视频的字幕校对员。输入是语音识别得到的句子，常见问题是同音字
和术语错误（例如「闭包」被识别成「弊包」，「时间复杂度」被识别成「事件复杂度」）。

规则：
1. 只修正错别字和明显错误的术语，不要改写、不要润色、不要增删内容。
2. 不要合并或拆分句子，不要改动语气词。
3. 不能确定的数字、公式、代码标识符保持原样。
4. 优先采用给定的术语表。
5. 人名、产品名、Bot 名是专有名词：术语表里的名字不要互相替换；
   原文已经写了明确名字时保持原样，只有明显的同音错字才修。
6. 只返回需要修改的句子。

输出 JSON：{"items": [{"i": 句子编号, "t": "修正后的文本"}]}
如果不需要修改，输出 {"items": []}
"""

MAX_BATCH_ITEMS = 60
MAX_BATCH_CHARS = 3000


class CorrectStage(Stage):
    name = "correct"
    title = "术语与错别字校正"
    version = 2
    requires = ("transcript",)

    def params(self, config: Config) -> dict:
        return {
            "enabled": config.asr.correct_with_llm,
            "glossary": config.asr.glossary,
            "llm": config.llm.model_dump(),
            "prompt_version": 2,
        }

    def skip(self, ctx: Context) -> str | None:
        if not ctx.config.asr.correct_with_llm:
            return "未开启 asr.correct_with_llm"
        env_name = ctx.config.llm.api_key_env
        if not os.environ.get(env_name):
            return f"未设置 {env_name}，跳过校正"
        return None

    def run(self, ctx: Context) -> BaseModel:
        transcript = ctx.load("transcript", Transcript)
        glossary = ctx.config.asr.glossary or transcript.hotwords
        if glossary:
            ctx.detail("术语表：" + "、".join(glossary[:10]) + ("…" if len(glossary) > 10 else ""))

        provider = create_llm_from_config(ctx.config.llm)
        corrected = transcript.model_copy(deep=True)

        batches = list(_batches(corrected.utterances))
        total = 0
        for index, batch in enumerate(batches, start=1):
            ctx.progress(f"校正中 {index}/{len(batches)}…")
            payload = provider.complete_json(
                SYSTEM_PROMPT, _build_user_prompt(batch, glossary), progress=ctx.progress
            )
            total += _apply(batch, payload.get("items") or [])

        corrected.corrections = total
        return corrected

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, Transcript)
        if result.corrections == 0:
            return f"{result.source} · 未发现需要修正的地方"
        return f"{result.source} · 修正 {result.corrections} 处 · {result.summary()}"


def _batches(
    utterances: list[Utterance],
    max_items: int = MAX_BATCH_ITEMS,
    max_chars: int = MAX_BATCH_CHARS,
) -> Iterator[list[Utterance]]:
    batch: list[Utterance] = []
    chars = 0
    for utterance in utterances:
        if batch and (len(batch) >= max_items or chars + len(utterance.text) > max_chars):
            yield batch
            batch, chars = [], 0
        batch.append(utterance)
        chars += len(utterance.text)
    if batch:
        yield batch


def _build_user_prompt(batch: list[Utterance], glossary: list[str]) -> str:
    lines = []
    if glossary:
        lines.append("术语表：" + "、".join(glossary))
        lines.append("")
    lines.append("句子：")
    lines.extend(f"{index}. {utterance.text}" for index, utterance in enumerate(batch))
    return "\n".join(lines)


def _apply(batch: list[Utterance], items: list[dict]) -> int:
    """应用修改，同时把明显跑偏的输出挡掉（长度突变、越界、空文本）。"""
    changed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("i"))
            text = str(item.get("t", "")).strip()
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(batch) or not text:
            continue

        original = batch[index].text
        ratio = len(text) / max(1, len(original))
        if not 0.5 <= ratio <= 1.6:
            continue  # 模型开始自由发挥了，丢弃
        if text == original:
            continue

        batch[index].text = text
        changed += 1
    return changed
