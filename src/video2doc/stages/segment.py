"""segment 阶段：让 LLM 找主题切换点，边界和时长由代码把关。"""

from __future__ import annotations

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..schema import Segmentation, SourceInfo
from ..writing import llm_unavailable, load_transcript, make_llm, resolve_language
from ..writing.segment import segment_transcript


class SegmentStage(Stage):
    name = "segment"
    title = "语义分段"
    version = 2
    requires = ("transcript",)

    def params(self, config: Config) -> dict:
        return {
            "enabled": config.write.enabled,
            "write": config.write.model_dump(exclude={"review", "max_rewrites", "enabled"}),
            "llm": config.llm.model_dump(),
            "render_title": config.render.title,
            "prompt_version": 1,
        }

    def skip(self, ctx: Context) -> str | None:
        if not ctx.config.write.enabled:
            return "未开启 write.enabled"
        return llm_unavailable(ctx.config)

    def run(self, ctx: Context) -> BaseModel:
        transcript = load_transcript(ctx)
        source = ctx.load("ingest", SourceInfo) if _done(ctx, "ingest") else None
        title = ctx.config.render.title or (source.title if source else "视频教程")
        language = resolve_language(ctx.config, transcript)
        ctx.detail(f"转写 {len(transcript.utterances)} 句 · 输出语言 {language}")

        provider = make_llm(ctx.config, ctx.config.write.temperature)
        result = segment_transcript(
            transcript,
            provider,
            ctx.config.write,
            title=title,
            language=language,
            progress=ctx.progress,
        )
        result.model = str(getattr(provider.config, "model", ""))
        return result

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, Segmentation)
        return result.summary()


def _done(ctx: Context, stage_name: str) -> bool:
    record = ctx.job.stages.get(stage_name)
    return record is not None and record.status == "done"
