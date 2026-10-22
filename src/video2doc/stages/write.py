"""write 阶段：逐节生成教程正文。"""

from __future__ import annotations

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..schema import Alignment, Document, Segmentation, SourceInfo
from ..writing import llm_unavailable, load_transcript, make_llm, resolve_language
from ..writing.write import write_document


class WriteStage(Stage):
    name = "write"
    title = "逐节写作"
    version = 1
    requires = ("segment", "align")

    def params(self, config: Config) -> dict:
        return {
            "enabled": config.write.enabled,
            "write": config.write.model_dump(),
            "llm": config.llm.model_dump(),
            "prompt_version": 1,
        }

    def skip(self, ctx: Context) -> str | None:
        if not ctx.config.write.enabled:
            return "未开启 write.enabled"
        return llm_unavailable(ctx.config)

    def run(self, ctx: Context) -> BaseModel:
        segmentation = ctx.load("segment", Segmentation)
        alignment = ctx.load("align", Alignment)
        transcript = load_transcript(ctx)
        source = ctx.load("ingest", SourceInfo) if _done(ctx, "ingest") else None
        language = resolve_language(ctx.config, transcript)
        title = ctx.config.render.title or (source.title if source else "视频教程")

        ctx.detail(
            f"{len(segmentation.sections)} 节 · {len(alignment.moments)} 张可用画面 · "
            f"语言 {language}"
        )
        provider = make_llm(ctx.config, ctx.config.write.temperature)
        document = write_document(
            segmentation,
            transcript,
            alignment.moments,
            provider,
            ctx.config.write,
            title=title,
            language=language,
            progress=ctx.progress,
        )
        return document

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, Document)
        return result.summary()


def _done(ctx: Context, stage_name: str) -> bool:
    record = ctx.job.stages.get(stage_name)
    return record is not None and record.status == "done"
