"""读取阶段产物：把读文件失败和 JSON/Pydantic 校验失败统一成 StageError。"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..errors import StageError

T = TypeVar("T", bound=BaseModel)


def load_model(
    path: Path,
    model: type[T],
    *,
    label: str = "JSON 文件",
    hint: str | None = None,
) -> T:
    """读取并校验一个 JSON 模型；失败时给出可执行的修复建议。"""
    try:
        # utf-8-sig：兼容 Windows 编辑器给 JSON 加上的 BOM
        return model.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise StageError(
            f"{label} 无法解析：{path}",
            hint=hint or "文件可能损坏；加上 --force 重跑，或删掉任务目录重跑。",
        ) from exc
