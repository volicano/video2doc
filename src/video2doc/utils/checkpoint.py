"""阶段内部的细粒度检查点。

整阶段的缓存由 pipeline 负责；这里的检查点只解决「跑到一半失败，
不想把已经完成的节 / 画面全部重做」的问题。成功收尾后由调用方删除。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_entries(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {str(key): value for key, value in entries.items() if isinstance(value, dict)}


def save_entries(path: Path | None, entries: dict[str, dict[str, Any]]) -> None:
    if path is None:
        return
    payload = {"version": 1, "entries": entries}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        # 检查点只是加速恢复，写不进去不该影响主流程
        return


def discard(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
