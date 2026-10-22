"""调 ffmpeg 抽帧。"""

from __future__ import annotations

from pathlib import Path

from ..errors import StageError
from ..utils.proc import ffmpeg_exe, run_cmd

ANALYSIS_PREFIX = "f_"


def auto_interval(duration: float) -> float:
    """短视频细采，长视频粗采，保证分析帧总数在一两千量级。"""
    if duration <= 1800:
        return 1.0
    if duration <= 3600:
        return 2.0
    return 3.0


def sample_frames(video: Path, out_dir: Path, *, interval: float, width: int = 320) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob(f"{ANALYSIS_PREFIX}*.jpg"):
        stale.unlink()

    pattern = out_dir / f"{ANALYSIS_PREFIX}%06d.jpg"
    run_cmd(
        [
            ffmpeg_exe(),
            "-nostdin",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"fps=1/{interval:g},scale={width}:-2",
            "-q:v",
            "4",
            str(pattern),
        ],
        timeout=7200,
    )

    frames = sorted(out_dir.glob(f"{ANALYSIS_PREFIX}*.jpg"))
    if not frames:
        raise StageError(
            "没有抽到任何画面",
            hint="确认输入的是视频文件；音频-only 的文件请先转成视频。",
        )
    return frames


def extract_frame(video: Path, time: float, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            ffmpeg_exe(),
            "-nostdin",
            "-y",
            "-ss",
            f"{max(0.0, time):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out_path),
        ],
        timeout=120,
    )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise StageError(f"抽帧失败：{out_path}")
    return out_path
