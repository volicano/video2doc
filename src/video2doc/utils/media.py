"""媒体探测与音轨抽取。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..errors import StageError
from .proc import ffmpeg_exe, ffprobe_exe, run_cmd


@dataclass
class MediaInfo:
    duration: float | None
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool
    format_name: str | None


def probe(path: Path) -> MediaInfo:
    if not path.exists():
        raise StageError(f"文件不存在：{path}")

    proc = run_cmd(
        [
            ffprobe_exe(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise StageError(f"无法解析 ffprobe 输出：{path}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}

    duration = _to_float(fmt.get("duration")) or _to_float((video or {}).get("duration"))
    return MediaInfo(
        duration=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        has_video=video is not None,
        has_audio=audio is not None,
        format_name=fmt.get("format_name"),
    )


def extract_audio(
    video: Path,
    out: Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """统一转成 16kHz 单声道 PCM，这是所有 ASR 模型的通用输入格式。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            ffmpeg_exe(),
            "-nostdin",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        timeout=1800,
    )
    if not out.exists() or out.stat().st_size == 0:
        raise StageError(f"音轨抽取失败，输出为空：{out}")
    return out


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
