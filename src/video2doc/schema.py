"""流水线中流转的数据结构。

全部用 pydantic 建模，好处是每个阶段的产物落盘后仍可校验，
上游改字段时下游会立刻报错，而不是默默算出垃圾。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class SubtitleFile(BaseModel):
    path: str
    language: str
    auto: bool = False
    format: str = "vtt"


class SourceInfo(BaseModel):
    """ingest 阶段的产物：视频是谁、在哪、多长、有没有现成字幕。"""

    kind: Literal["url", "file"]
    path: str
    title: str
    duration: float | None = None
    uploader: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    webpage_url: str | None = None
    video_id: str | None = None
    subtitles: list[SubtitleFile] = Field(default_factory=list)
    downloaded: bool = False
    thumbnail: str | None = None


class AudioInfo(BaseModel):
    path: str
    duration: float | None = None
    sample_rate: int = 16000
    channels: int = 1
    size_bytes: int = 0


class Utterance(BaseModel):
    """一句话，带时间戳。时间戳是整个系统的锚点，不能是 LLM 编的。"""

    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Transcript(BaseModel):
    source: Literal["subtitles", "asr"]
    provider: str
    model: str | None = None
    language: str = "zh"
    duration: float | None = None
    hotwords: list[str] = Field(default_factory=list)
    # 经过 LLM 校正的句子数（0 表示没校正过）
    corrections: int = 0
    utterances: list[Utterance] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(u.text for u in self.utterances if u.text.strip())

    @property
    def char_count(self) -> int:
        return sum(len(u.text) for u in self.utterances)

    @property
    def coverage(self) -> float:
        """语音覆盖时长 / 视频时长，用来发现“识别漏了一大半”的情况。"""
        if not self.duration or self.duration <= 0:
            return 0.0
        return min(1.0, sum(u.duration for u in self.utterances) / self.duration)

    def summary(self) -> str:
        minutes = (self.duration or 0) / 60
        return (
            f"{len(self.utterances)} 句 / {self.char_count} 字 "
            f"/ 覆盖 {self.coverage:.0%} / {minutes:.1f} 分钟"
        )


class StageRecord(BaseModel):
    name: str
    status: Literal["done", "failed", "skipped"]
    fingerprint: str
    output: str | None = None
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""
    elapsed: float = 0.0
    summary: str = ""


class Job(BaseModel):
    """跑一次任务的唯一真相源，落到 <job_dir>/job.json。"""

    schema_version: int = SCHEMA_VERSION
    job_id: str
    target: str
    created_at: str
    updated_at: str
    config_snapshot: dict = Field(default_factory=dict)
    config_hash: str = ""
    source: SourceInfo | None = None
    stages: dict[str, StageRecord] = Field(default_factory=dict)


# ---------------------------------------------------------------- 画面分析


class FrameCurvePoint(BaseModel):
    """信息密度曲线上的一个点，用于调参与 debug 页面。"""

    t: float
    edge: float
    skin: float
    diff: float


class FrameCandidate(BaseModel):
    index: int
    time: float
    path: str
    shot_start: float
    shot_end: float
    kind: str = "screen"
    score: float = 0.0
    edge: float = 0.0
    skin: float = 0.0
    sharpness: float = 0.0
    hash: str = ""


class FrameSelection(BaseModel):
    strategy: str = "info-density"
    interval: float = 1.0
    sampled: int = 0
    shots: int = 0
    duration: float = 0.0
    frames: list[FrameCandidate] = Field(default_factory=list)
    curve: list[FrameCurvePoint] = Field(default_factory=list)

    def summary(self) -> str:
        return f"采样 {self.sampled} 帧 / {self.shots} 个镜头 → 选中 {len(self.frames)} 张"


# ------------------------------------------------------------------- OCR


class TextBlock(BaseModel):
    text: str
    confidence: float = 0.0
    box: list[float] = Field(default_factory=list)  # x0, y0, x1, y1


class FrameText(BaseModel):
    index: int
    text: str = ""
    blocks: list[TextBlock] = Field(default_factory=list)
    char_count: int = 0
    mean_confidence: float | None = None


class OCRResult(BaseModel):
    provider: str = ""
    language: str = "ch"
    frames: list[FrameText] = Field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(frame.char_count for frame in self.frames)

    def summary(self) -> str:
        with_text = sum(1 for frame in self.frames if frame.char_count > 0)
        return f"{with_text}/{len(self.frames)} 张有文字 · 共 {self.total_chars} 字"


# ---------------------------------------------------------------- 图文对齐


class VisualMoment(BaseModel):
    """一张图 + 它覆盖的时间窗 + 这段窗口里的画面文字与口播内容。"""

    index: int
    image: str
    time: float
    start: float
    end: float
    kind: str = "screen"
    score: float = 0.0
    ocr_text: str = ""
    spoken_text: str = ""
    utterances: int = 0
    extended: bool = False


class Alignment(BaseModel):
    duration: float = 0.0
    moments: list[VisualMoment] = Field(default_factory=list)
    coverage: float = 0.0
    gaps: list[list[float]] = Field(default_factory=list)
    debug_html: str | None = None

    def summary(self) -> str:
        return f"{len(self.moments)} 个画面 · 覆盖 {self.coverage:.0%}"
