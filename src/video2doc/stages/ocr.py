"""ocr 阶段：把选中画面里的文字读出来。

它和 frames 分开，是因为 OCR 引擎是可选的：
没装就跳过，后面的对齐仍然能只用口播文本工作，只是少一维信息。
"""

from __future__ import annotations

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..providers.ocr import create_ocr, resolve_name
from ..schema import FrameSelection, FrameText, OCRResult


class OCRStage(Stage):
    name = "ocr"
    title = "识别画面文字"
    version = 1
    requires = ("frames",)

    def params(self, config: Config) -> dict:
        return config.ocr.model_dump()

    def skip(self, ctx: Context) -> str | None:
        resolved = resolve_name(ctx.config.ocr.provider)
        if resolved == "none":
            return '未安装 OCR 引擎（pip install "video2doc[ocr]"）'
        frames = ctx.load("frames", FrameSelection)
        if not frames.frames:
            return "没有可用画面"
        return None

    def run(self, ctx: Context) -> BaseModel:
        frames = ctx.load("frames", FrameSelection)
        config = ctx.config.ocr
        provider = create_ocr(config)

        results: list[FrameText] = []
        total = len(frames.frames)
        for order, frame in enumerate(frames.frames, 1):
            ctx.progress(f"识别画面文字 {order}/{total}…")
            path = ctx.job_dir / frame.path
            blocks = [
                block for block in provider.read(path) if block.confidence >= config.min_confidence
            ]
            text = "\n".join(block.text for block in blocks)
            confidences = [block.confidence for block in blocks]
            results.append(
                FrameText(
                    index=frame.index,
                    text=text,
                    blocks=blocks,
                    char_count=len(text.replace("\n", "")),
                    mean_confidence=(
                        round(sum(confidences) / len(confidences), 4) if confidences else None
                    ),
                )
            )

        return OCRResult(provider=provider.name, language=config.language, frames=results)

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, OCRResult)
        return result.summary()
