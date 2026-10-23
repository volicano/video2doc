"""critique 阶段：确定性质检，必要时定向重写。"""

from __future__ import annotations

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..schema import Alignment, Critique, Document, Segmentation
from ..writing import llm_unavailable, load_transcript, make_llm, resolve_language
from ..writing.critique import critique_document


class CritiqueStage(Stage):
    name = "critique"
    title = "质量检查"
    version = 5
    requires = ("write", "segment", "align")

    def params(self, config: Config) -> dict:
        return {
            "enabled": config.write.enabled,
            "review": config.write.review,
            "max_rewrites": config.write.max_rewrites,
            "llm": config.llm.model_dump() if config.write.review else {},
            "prompt_version": 3,
        }

    def skip(self, ctx: Context) -> str | None:
        if not ctx.config.write.enabled:
            return "未开启 write.enabled"
        return llm_unavailable(ctx.config)

    def run(self, ctx: Context) -> BaseModel:
        document = ctx.load("write", Document)
        segmentation = ctx.load("segment", Segmentation)
        alignment = ctx.load("align", Alignment)
        transcript = load_transcript(ctx)

        provider = None
        if ctx.config.write.review:
            provider = make_llm(ctx.config, ctx.config.write.temperature)
        else:
            ctx.detail("确定性检查（未开启 write.review，不调用 LLM）")

        critique = critique_document(
            document,
            segmentation,
            transcript,
            alignment.moments,
            provider=provider,
            config=ctx.config.write,
            language=resolve_language(ctx.config, transcript),
            progress=ctx.progress,
        )
        for issue in critique.issues[:6]:
            ctx.detail(f"· {issue.message}")
        return critique

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, Critique)
        return result.summary()
