"""RapidOCR（PP-OCR 模型的 ONNX 版）。

选它而不是 PaddleOCR 的原因很实在：不用装 paddlepaddle，
onnxruntime 一个包搞定，Windows 上装得动，中文和英文混排效果够用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import StageError, ToolMissingError
from ...schema import TextBlock
from .base import OCRProvider, register


@register
class RapidOCRProvider(OCRProvider):
    name = "rapidocr"
    label = "RapidOCR（ONNX，轻量）"
    requires_module = "rapidocr_onnxruntime"
    parallel_safe = True

    def __init__(self, config) -> None:  # noqa: ANN001 - 与基类保持一致
        super().__init__(config)
        self._engine: Any = None

    def warmup(self) -> None:
        self._engine_instance()

    def read(self, image: Path) -> list[TextBlock]:
        engine = self._engine_instance()
        try:
            result, _ = engine(str(image))
        except Exception as exc:  # noqa: BLE001 - OCR 引擎的异常类型不稳定
            raise StageError(f"OCR 失败：{image.name}（{type(exc).__name__}: {exc}）") from exc
        return _to_blocks(result or [])

    def _engine_instance(self):
        if self._engine is not None:
            return self._engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ToolMissingError(
                "未安装 rapidocr-onnxruntime",
                hint='pip install "video2doc[ocr]"',
            ) from exc
        self._engine = RapidOCR()
        return self._engine


def _to_blocks(raw: list) -> list[TextBlock]:
    """RapidOCR 返回 [[四点坐标, 文本, 置信度], ...]。"""
    blocks: list[TextBlock] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        box = item[0]
        text = str(item[1]).strip()
        if not text:
            continue
        confidence = float(item[2]) if len(item) > 2 else 1.0
        blocks.append(
            TextBlock(
                text=text,
                confidence=round(confidence, 4),
                box=_bounding_box(box),
            )
        )

    # 按行排序：先看 y，再在同一行内按 x
    blocks.sort(
        key=lambda block: (
            round(block.box[1] / 12) if block.box else 0,
            block.box[0] if block.box else 0,
        )
    )
    return blocks


def _bounding_box(box: Any) -> list[float]:
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except (TypeError, ValueError, IndexError):
        return []
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]
