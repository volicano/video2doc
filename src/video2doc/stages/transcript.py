"""transcript 阶段：字幕优先，没有字幕才跑语音识别。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..config import Config
from ..errors import StageError
from ..hotwords import resolve_hotwords
from ..pipeline import Context, Stage
from ..providers.asr import create_provider
from ..schema import AudioInfo, SourceInfo, Transcript
from ..subtitles import parse_subtitle_file, pick_subtitle


class TranscriptStage(Stage):
    name = "transcript"
    title = "生成带时间戳的文本"
    version = 1
    requires = ("ingest", "audio")

    def params(self, config: Config) -> dict:
        return {
            "prefer_subtitles": config.video.prefer_subtitles,
            "language": config.asr.language,
            # 校正相关字段属于 downstream，不应该让 ASR 重跑
            "asr": config.asr.model_dump(exclude={"correct_with_llm", "glossary"}),
        }

    def run(self, ctx: Context) -> BaseModel:
        source = ctx.load("ingest", SourceInfo)
        language = ctx.config.asr.language

        if ctx.config.video.prefer_subtitles:
            subtitle = pick_subtitle(source.subtitles, language)
            if subtitle is not None:
                kind = "（自动生成）" if subtitle.auto else ""
                ctx.progress(f"读取字幕：{subtitle.language}{kind}")
                cues = parse_subtitle_file(
                    Path(subtitle.path), merge_gap=0.35 if subtitle.auto else 0.0
                )
                if cues:
                    return Transcript(
                        source="subtitles",
                        provider=f"subtitle:{subtitle.format}",
                        model=None,
                        language=subtitle.language,
                        duration=source.duration,
                        utterances=cues,
                    )
                ctx.detail("字幕文件解析为空，回退到语音识别。")

        audio = self._require_audio(ctx)
        hotwords = resolve_hotwords(ctx.config.asr.hotwords, ctx.config.asr.auto_hotwords, source)
        if hotwords:
            ctx.detail("热词：" + "、".join(hotwords[:12]) + ("…" if len(hotwords) > 12 else ""))

        provider = create_provider(ctx.config.asr.provider, ctx.config.asr)
        ctx.progress(f"{provider.label} 识别中…")
        utterances = provider.transcribe(
            Path(audio.path),
            language=language,
            hotwords=hotwords,
            initial_prompt=ctx.config.asr.initial_prompt,
            progress=ctx.progress,
        )
        if not utterances:
            raise StageError(
                "语音识别没有返回任何内容",
                hint="检查音轨是否有声音（先用播放器确认），或换用其他 asr.provider。",
            )
        if all(u.end <= 0 for u in utterances):
            ctx.detail("⚠ 该 ASR 服务没有返回时间戳，后续配图与文字将无法精确对齐。")

        return Transcript(
            source="asr",
            provider=provider.name,
            model=ctx.config.asr.model,
            language=language,
            duration=audio.duration,
            hotwords=hotwords,
            utterances=utterances,
        )

    def summary(self, result: BaseModel) -> str:
        assert isinstance(result, Transcript)
        return f"{result.provider} · {result.summary()}"

    def _require_audio(self, ctx: Context) -> AudioInfo:
        record = ctx.job.stages.get("audio")
        if record is None or record.status != "done":
            raise StageError(
                "需要语音识别，但音轨阶段被跳过了",
                hint="加上 `--from audio` 重新跑一次。",
            )
        return ctx.load("audio", AudioInfo)
