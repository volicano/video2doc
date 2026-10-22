"""frames 阶段：先把整条视频看一遍，再挑出值得配图的画面。

两步走的原因：一小时的视频有几千帧，逐帧跑 OCR 要几十分钟；
而「哪一帧值得看」用边缘密度 + 肤色占比就能判断个八九不离十。
所以先用小图快速打分、切镜头、选代表帧，再回到原视频把选中帧抽成原图。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from ..config import Config
from ..errors import StageError
from ..pipeline import Context, Stage
from ..schema import FrameCandidate, FrameCurvePoint, FrameSelection, SourceInfo
from ..visual import stats
from ..visual.extract import auto_interval, extract_frame, sample_frames
from ..visual.shots import Sample, build_shots, kind_of, select_shots


class FramesStage(Stage):
    name = "frames"
    title = "抽帧与画面分析"
    version = 1
    requires = ("ingest",)

    def params(self, config: Config) -> dict:
        return config.frames.model_dump()

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, FrameSelection)
        return result.summary()

    def run(self, ctx: Context) -> BaseModel:
        source = ctx.load("ingest", SourceInfo)
        return run_frames(ctx, source)


def run_frames(ctx: Context, source: SourceInfo) -> FrameSelection:
    config = ctx.config.frames
    video = Path(source.path)
    duration = float(source.duration or 0.0)
    interval = config.sample_interval_sec or auto_interval(duration or 3600.0)

    analysis_dir = ctx.job_dir / "cache" / "analysis"
    ctx.progress(f"抽帧中（每 {interval:g}s 一帧，小图）…")
    paths = sample_frames(video, analysis_dir, interval=interval, width=config.analysis_width)
    if duration <= 0:
        duration = len(paths) * interval

    ctx.progress(f"分析 {len(paths)} 帧画面…")
    samples, curve = _analyze(paths, interval, ctx)

    ctx.progress("切分镜头…")
    shots = build_shots(
        samples,
        duration=duration,
        interval=interval,
        change_threshold=config.change_threshold,
        hash_threshold=config.hash_threshold,
    )

    if config.strategy == "uniform":
        picks = [
            (sample, sample.time - 1.0, sample.time + 1.0)
            for sample in _uniform(samples, config.max_frames)
        ]
    else:
        chosen = select_shots(
            shots,
            duration=duration,
            max_frames=config.max_frames,
            min_gap_sec=config.min_gap_sec,
            coverage_buckets=config.coverage_buckets,
            min_score=config.min_shot_score,
        )
        picks = [(shot.pick, shot.start, shot.end) for shot in chosen if shot.pick is not None]

    if not picks:
        raise StageError(
            "没有选出任何画面",
            hint='把 frames.min_shot_score 调低，或改用 frames.strategy = "uniform"。',
        )

    candidates = _materialize(ctx, video, picks, duration)
    kept = _dedupe(candidates, ctx.job_dir / "frames")

    selection = FrameSelection(
        strategy=config.strategy,
        interval=round(interval, 3),
        sampled=len(paths),
        shots=len(shots),
        duration=round(duration, 3),
        frames=kept,
        curve=curve,
    )

    # 分析用的小图占几十上百 MB，选完就没用了
    shutil.rmtree(analysis_dir, ignore_errors=True)
    return selection


# ------------------------------------------------------------------ 实现


def _analyze(
    paths: list[Path], interval: float, ctx: Context
) -> tuple[list[Sample], list[FrameCurvePoint]]:
    samples: list[Sample] = []
    curve: list[FrameCurvePoint] = []
    previous: np.ndarray | None = None
    total = len(paths)

    for index, path in enumerate(paths):
        stat, gray = stats.analyze(path, previous)
        previous = gray
        time = index * interval
        if index == 0:
            stat.diff = 0.0

        samples.append(Sample(index=index, time=time, stat=stat, hash=stats.dhash_gray(gray)))
        curve.append(
            FrameCurvePoint(
                t=round(time, 3),
                edge=round(stat.edge, 4),
                skin=round(stat.skin, 4),
                diff=round(stat.diff, 4),
            )
        )
        if index % 200 == 0 and index:
            ctx.progress(f"分析画面 {index}/{total}…")

    return samples, curve


def _uniform(samples: list[Sample], limit: int) -> list[Sample]:
    if limit <= 0:
        return []
    step = max(1, len(samples) // limit)
    return samples[::step][:limit]


def _materialize(
    ctx: Context,
    video: Path,
    picks: list[tuple[Sample, float, float]],
    duration: float,
) -> list[FrameCandidate]:
    frames_dir = ctx.job_dir / "frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[FrameCandidate] = []
    paths: dict[int, Path] = {}

    for order, (sample, shot_start, shot_end) in enumerate(picks, 1):
        ctx.progress(f"抽取原图 {order}/{len(picks)}…")
        name = f"f{order:02d}_{int(round(sample.time)):05d}s.jpg"
        path = extract_frame(video, sample.time, frames_dir / name)
        sharp = stats.sharpness(stats.load_gray(path, width=640))
        paths[order] = path
        candidates.append(
            FrameCandidate(
                index=order,
                time=round(sample.time, 3),
                path=path.relative_to(ctx.job_dir).as_posix(),
                shot_start=round(shot_start, 3),
                shot_end=round(min(shot_end, duration), 3),
                kind=kind_of(sample),
                score=round(stats.info_score(sample.stat.edge, sample.stat.skin), 4),
                edge=round(sample.stat.edge, 4),
                skin=round(sample.stat.skin, 4),
                sharpness=round(sharp, 1),
                hash=str(stats.dhash(path)),
            )
        )

    _retry_blurry(ctx, video, candidates, paths, duration)
    return candidates


def _retry_blurry(
    ctx: Context,
    video: Path,
    candidates: list[FrameCandidate],
    paths: dict[int, Path],
    duration: float,
) -> None:
    """运动模糊是瞬时现象，前后半秒通常就有一张清楚的。

    只在整批里明显偏糊时才重抽，避免为每一帧多跑两次 ffmpeg。
    """
    if not candidates:
        return
    median = float(np.median([candidate.sharpness for candidate in candidates]))
    floor = max(12.0, median * 0.35)
    # 人脸镜头本来就该是柔和的，重抽也救不回文字，纯属浪费 ffmpeg 调用
    blurry = [
        candidate
        for candidate in candidates
        if candidate.sharpness < floor and candidate.kind != "face"
    ]
    if not blurry:
        return

    ctx.detail(f"{len(blurry)} 张画面偏糊，尝试在镜头内换一张…")
    for candidate in blurry:
        frame_path = paths[candidate.index]
        best: tuple[Path, float, float] | None = None
        upper = max(candidate.shot_start + 0.1, min(candidate.shot_end, duration) - 0.1)
        for delta in (-0.5, 0.5):
            when = min(max(candidate.time + delta, candidate.shot_start + 0.1), upper)
            if abs(when - candidate.time) < 0.05:
                continue
            alternative = frame_path.with_name(f"{frame_path.stem}_alt.jpg")
            try:
                extract_frame(video, when, alternative)
            except StageError:
                continue
            sharp = stats.sharpness(stats.load_gray(alternative, width=640))
            if best is None or sharp > best[1]:
                best = (alternative, sharp, when)

        if best is not None and best[1] > candidate.sharpness * 1.15:
            best[0].replace(frame_path)
            candidate.time = round(best[2], 3)
            candidate.sharpness = round(best[1], 1)
        elif best is not None:
            best[0].unlink(missing_ok=True)


def _dedupe(candidates: list[FrameCandidate], frames_dir: Path) -> list[FrameCandidate]:
    """同一张 PPT 反复出现时只留第一次，按时间遍历保证时间顺序不乱。"""
    kept: list[FrameCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.time):
        value = int(candidate.hash or "0")
        if any(stats.distance(value, int(other.hash or "0")) <= 2 for other in kept):
            (frames_dir / Path(candidate.path).name).unlink(missing_ok=True)
            continue
        kept.append(candidate)

    kept.sort(key=lambda item: item.time)
    for index, candidate in enumerate(kept, 1):
        candidate.index = index
    return kept
