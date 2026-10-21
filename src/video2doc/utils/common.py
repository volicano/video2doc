"""零碎但通用的工具。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(payload: Any) -> str:
    return sha256_text(canonical_json(payload))


def hms(seconds: float | None) -> str:
    """秒 -> 00:12:30，用于日志和后续的时间戳链接。"""
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"
