"""render 阶段：把文档落成单页 HTML，同时导出一份 Markdown。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..config import Config
from ..overrides import OVERRIDES_NAME, apply_overrides, load_overrides, overrides_digest
from ..pipeline import Context, Stage
from ..render import build_html, build_markdown
from ..schema import Critique, Document, RenderedDoc, SourceInfo

HTML_NAME = "tutorial.html"
MARKDOWN_NAME = "tutorial.md"


class RenderStage(Stage):
    name = "render"
    title = "生成单页教程"
    version = 3
    requires = ("write", "critique")

    def params(self, config: Config) -> dict:
        return {
            "enabled": config.write.enabled,
            "render": config.render.model_dump(),
        }

    def local_params(self, job_dir: Path) -> dict:
        # 人工覆盖层变了就要重新出 HTML，但它不该影响 write 的缓存
        return {"overrides": overrides_digest(job_dir)}

    def skip(self, ctx: Context) -> str | None:
        if not ctx.config.write.enabled:
            return "未开启 write.enabled"
        if not _done(ctx, "write"):
            return "还没有可渲染的文档"
        return None

    def run(self, ctx: Context) -> BaseModel:
        document = ctx.load("write", Document)
        critique = ctx.load("critique", Critique) if _done(ctx, "critique") else Critique()
        source = ctx.load("ingest", SourceInfo) if _done(ctx, "ingest") else None

        overrides = load_overrides(ctx.job_dir)
        if overrides is not None and not overrides.is_empty():
            document, warnings = apply_overrides(document, overrides)
            for warning in warnings:
                ctx.detail(f"覆盖层：{warning}")
            ctx.detail(f"已应用 {OVERRIDES_NAME}")

        title = ctx.config.render.title or document.title

        html_text = build_html(
            document,
            source,
            critique,
            job_dir=ctx.job_dir,
            config=ctx.config.render,
            title=title,
        )
        markdown = build_markdown(
            document, source, title=title, captions=ctx.config.render.image_captions
        )

        html_path = ctx.job_dir / HTML_NAME
        md_path = ctx.job_dir / MARKDOWN_NAME
        html_path.write_text(html_text, encoding="utf-8")
        md_path.write_text(markdown, encoding="utf-8")

        size = html_path.stat().st_size
        embedded = "图片已内嵌" if ctx.config.render.embed_images else "图片外链"
        ctx.detail(f"{html_path} （{size / 1024:.0f} KB，{embedded}）")
        return RenderedDoc(
            html=HTML_NAME,
            markdown=MARKDOWN_NAME,
            title=title,
            size_bytes=size,
            sections=len(document.sections),
            images=document.images,
        )

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, RenderedDoc)
        return result.summary()


def _done(ctx: Context, stage_name: str) -> bool:
    record = ctx.job.stages.get(stage_name)
    return record is not None and record.status == "done"
