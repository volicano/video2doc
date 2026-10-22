"""align 阶段：把「一张图」和「这段时间在讲什么」绑在一起。

M3 写作时最需要的不是一句句话，而是 (图, 画面文字, 这段口播) 三元组。
这里不做语义分段——那是写作阶段的事，而且需要 LLM；
这一阶段只做确定性的时间对齐，保证 LLM 拿到的时间戳和图片永远不会张冠李戴。
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..schema import (
    Alignment,
    FrameCandidate,
    FrameSelection,
    OCRResult,
    SourceInfo,
    Transcript,
    Utterance,
    VisualMoment,
)
from ..utils.common import hms
from ..visual.contact import render_debug_html

# 没有口播的镜头（例如静默展示一张图），向前后借多久的语音
_EXTEND_SEC = 20.0
# 超过这么久没有配图，就在报告里点名
_GAP_SEC = 60.0
_CODE_CHARS = set("{}();=<>[]")


class AlignStage(Stage):
    name = "align"
    title = "图文对齐"
    version = 1
    requires = ("frames", "transcript")

    def params(self, config: Config) -> dict:
        return {"extend_sec": _EXTEND_SEC, "debug_html": config.render.debug_html}

    def run(self, ctx: Context) -> BaseModel:
        frames = ctx.load("frames", FrameSelection)
        transcript = ctx.load("transcript", Transcript)
        source = ctx.load("ingest", SourceInfo) if _done(ctx, "ingest") else None
        ocr = ctx.load("ocr", OCRResult) if _done(ctx, "ocr") else None

        texts = {frame.index: frame.text for frame in ocr.frames} if ocr else {}

        moments: list[VisualMoment] = []
        for order, frame in enumerate(frames.frames, 1):
            ctx.progress(f"对齐画面 {order}/{len(frames.frames)}…")
            start, end = _window(frame, frames.duration)
            hits = _spoken(transcript.utterances, start, end)
            extended = False
            if not hits:
                hits = _nearest(transcript.utterances, start, end, _EXTEND_SEC)
                extended = bool(hits)

            ocr_text = texts.get(frame.index, "")
            moments.append(
                VisualMoment(
                    index=frame.index,
                    image=frame.path,
                    time=frame.time,
                    start=round(start, 3),
                    end=round(end, 3),
                    kind=_refine_kind(frame.kind, ocr_text),
                    score=frame.score,
                    ocr_text=ocr_text,
                    spoken_text=_join_text([utterance.text for utterance in hits]),
                    utterances=len(hits),
                    extended=extended,
                )
            )

        # 时间轴上的 moment 必须按时间排，写作阶段才能顺着讲
        moments.sort(key=lambda moment: moment.time)
        alignment = Alignment(
            duration=round(frames.duration, 3),
            moments=moments,
            coverage=_coverage(moments, frames.duration),
            gaps=_gaps(moments, frames.duration),
        )

        if ctx.config.render.debug_html:
            dest = ctx.job_dir / "preview.html"
            render_debug_html(
                dest,
                job_dir=ctx.job_dir,
                source=source,
                selection=frames,
                ocr=ocr,
                alignment=alignment,
                transcript=transcript,
            )
            alignment.debug_html = dest.relative_to(ctx.job_dir).as_posix()
            ctx.detail(f"调试图：{dest}")

        return alignment

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, Alignment)
        return result.summary()


# ------------------------------------------------------------------ 纯函数


def _window(frame: FrameCandidate, duration: float) -> tuple[float, float]:
    """镜头的时间范围就是这张图「负责」的时间范围。

    镜头边界由画面变化检测给出，天然互不重叠；退化成单帧视频时给个 ±5s 兜底。
    """
    start = max(0.0, frame.shot_start)
    end = min(duration, frame.shot_end) if duration > 0 else frame.shot_end
    if end <= start:
        start = max(0.0, frame.time - 5.0)
        end = frame.time + 5.0
        if duration > 0:
            end = min(duration, end)
    return start, end


def _spoken(utterances: list[Utterance], start: float, end: float) -> list[Utterance]:
    return [
        utterance for utterance in utterances if utterance.end > start and utterance.start < end
    ]


def _nearest(utterances: list[Utterance], start: float, end: float, span: float) -> list[Utterance]:
    before = [u for u in utterances if u.end <= start and start - u.end <= span][-2:]
    after = [u for u in utterances if u.start >= end and u.start - end <= span][:2]
    return before + after


def _join_text(texts: list[str]) -> str:
    cleaned = [text.strip() for text in texts if text.strip()]
    if not cleaned:
        return ""
    joined = "".join(cleaned)
    if re.search(r"[\u4e00-\u9fff]", joined):
        return "".join(cleaned)
    return " ".join(cleaned)


def _refine_kind(kind: str, ocr_text: str) -> str:
    """OCR 是区分「代码」和「PPT」最可靠的信号：符号密度骗不了人。"""
    if not ocr_text:
        return kind
    hits = sum(ocr_text.count(char) for char in _CODE_CHARS)
    if hits >= 6:
        return "code"
    if kind in ("screen", "whiteboard") and len(ocr_text) >= 30:
        return "slide"
    return kind


def _coverage(moments: list[VisualMoment], duration: float) -> float:
    if duration <= 0:
        return 0.0
    total = sum(max(0.0, moment.end - moment.start) for moment in moments)
    return round(min(1.0, total / duration), 4)


def _gaps(moments: list[VisualMoment], duration: float) -> list[list[float]]:
    gaps: list[list[float]] = []
    previous_end = 0.0
    for moment in sorted(moments, key=lambda item: item.start):
        if moment.start - previous_end >= _GAP_SEC:
            gaps.append([round(previous_end, 1), round(moment.start, 1)])
        previous_end = max(previous_end, moment.end)
    if duration > 0 and duration - previous_end >= _GAP_SEC:
        gaps.append([round(previous_end, 1), round(duration, 1)])
    return gaps


def _done(ctx: Context, stage_name: str) -> bool:
    record = ctx.job.stages.get(stage_name)
    return record is not None and record.status == "done"


def describe_gaps(alignment: Alignment) -> str:
    if not alignment.gaps:
        return ""
    listed = "、".join(f"{hms(start)}–{hms(end)}" for start, end in alignment.gaps[:4])
    more = f" 等 {len(alignment.gaps)} 段" if len(alignment.gaps) > 4 else ""
    return f"{listed}{more} 没有配图"
