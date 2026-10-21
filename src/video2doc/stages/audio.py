"""audio 阶段：抽 16kHz 单声道 wav。

已有可用字幕时会自动跳过，省掉一次无意义的转码。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..config import Config
from ..pipeline import Context, Stage
from ..schema import AudioInfo, SourceInfo
from ..subtitles import parse_subtitle_file, pick_subtitle
from ..utils.common import human_size
from ..utils.media import extract_audio, probe


class AudioStage(Stage):
    name = "audio"
    title = "抽取音轨"
    version = 1
    requires = ("ingest",)

    def params(self, config: Config) -> dict:
        # 把「是否可能跳过」也纳入指纹：
        # 否则用户关掉 prefer_subtitles 后，这里仍是缓存里的 skipped，下游会拿不到音轨。
        return {
            "sample_rate": 16000,
            "channels": 1,
            "prefer_subtitles": config.video.prefer_subtitles,
            "language": config.asr.language,
        }

    def skip(self, ctx: Context) -> str | None:
        source = ctx.load("ingest", SourceInfo)
        if not ctx.config.video.prefer_subtitles or not source.subtitles:
            return None
        subtitle = pick_subtitle(source.subtitles, ctx.config.asr.language)
        if subtitle is None:
            return None
        try:
            cues = parse_subtitle_file(Path(subtitle.path))
        except OSError:
            return None
        if not cues:
            return None
        kind = "自动" if subtitle.auto else "人工"
        return f"已有{kind}字幕（{subtitle.language}），无需音轨"

    def run(self, ctx: Context) -> BaseModel:
        source = ctx.load("ingest", SourceInfo)
        out = ctx.path("media", "audio.wav")
        ctx.progress("ffmpeg 转码中…")
        extract_audio(Path(source.path), out)
        media = probe(out)
        return AudioInfo(
            path=str(out),
            duration=media.duration or source.duration,
            sample_rate=16000,
            channels=1,
            size_bytes=out.stat().st_size,
        )

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, AudioInfo)
        return f"16kHz mono · {human_size(result.size_bytes)}"
