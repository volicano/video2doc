"""外部命令调用。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..errors import ToolMissingError

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

_FFMPEG_HINT = (
    "Windows: winget install Gyan.FFmpeg  |  macOS: brew install ffmpeg  |  "
    "Linux: sudo apt install ffmpeg。装完重开终端，或用 v2doc doctor 检查。"
)


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 有些用户只装了 imageio-ffmpeg，能救急
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 任何导入/下载失败都视为没有
        raise ToolMissingError("找不到 ffmpeg", hint=_FFMPEG_HINT) from None


def ffprobe_exe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    try:
        sibling = Path(ffmpeg_exe()).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))
        if sibling.exists():
            return str(sibling)
    except ToolMissingError:
        pass
    raise ToolMissingError("找不到 ffprobe", hint=_FFMPEG_HINT)


def run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    """执行命令，失败时把 stderr 尾部带进异常，方便用户自查。"""
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=capture_output,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise ToolMissingError(f"命令不存在：{args[0]}", hint=_FFMPEG_HINT) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolMissingError(f"命令超时：{' '.join(args[:2])}") from exc

    if proc.returncode != 0:
        tail = _tail(proc.stderr or proc.stdout or "")
        raise ToolMissingError(
            f"命令执行失败（exit {proc.returncode}）：{' '.join(args[:3])} …\n{tail}"
        )
    return proc


def _tail(text: str, lines: int = 12) -> str:
    interesting = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(interesting[-lines:])
