"""本地 faster-whisper。离线、免费，是默认方案。"""

from __future__ import annotations

import inspect
import math
import os
import sys
from pathlib import Path

from ...errors import StageError, ToolMissingError
from ...hotwords import build_initial_prompt
from ...schema import Utterance
from ...utils.common import hms
from .base import ASRProvider, ProgressFn, register

# os.add_dll_directory 返回的句柄必须一直持有，否则目录会从 DLL 搜索路径里移除
_DLL_HANDLES: list[object] = []


def _enable_nvidia_dlls() -> None:
    """把通过 pip 安装的 CUDA 运行库加入 DLL 搜索路径。

    Windows 上 ctranslate2 不会自动发现 site-packages/nvidia/*/bin，
    用户即使装了 nvidia-cublas-cu12 / nvidia-cudnn-cu12 也会报 DLL not found。
    """
    if sys.platform != "win32":
        return
    try:
        import nvidia
    except ImportError:
        return
    roots = [Path(p) for p in getattr(nvidia, "__path__", []) if p]
    if not roots and nvidia.__file__:
        roots = [Path(nvidia.__file__).parent]
    for root in roots:
        for bin_dir in sorted(root.glob("*/bin")):
            if not bin_dir.is_dir():
                continue
            resolved = str(bin_dir.resolve())
            if resolved in os.environ.get("PATH", "").split(os.pathsep):
                continue
            try:
                _DLL_HANDLES.append(os.add_dll_directory(resolved))
            except OSError:
                continue
            os.environ["PATH"] = resolved + os.pathsep + os.environ.get("PATH", "")


@register
class FasterWhisperProvider(ASRProvider):
    name = "faster-whisper"
    label = "本地 faster-whisper"
    requires_module = "faster_whisper"

    def transcribe(
        self,
        audio: Path,
        *,
        language: str,
        hotwords: list[str],
        initial_prompt: str | None = None,
        progress: ProgressFn | None = None,
    ) -> list[Utterance]:
        _enable_nvidia_dlls()
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ToolMissingError(
                "未安装 faster-whisper",
                hint='pip install "video2doc[local]"，或把 asr.provider 改成 openai 走云端。',
            ) from exc

        device = _pick_device(self.config.device)
        compute_type = _pick_compute(device, self.config.compute_type)
        if progress:
            progress(f"加载 {self.config.model}（{device}/{compute_type}），首次运行会下载模型…")

        try:
            model = _load_model(WhisperModel, self.config.model, device, compute_type)
        except StageError:
            if device == "cpu":
                raise
            if progress:
                progress("CUDA 初始化失败，回退 CPU/int8 继续…")
            device, compute_type = "cpu", "int8"
            model = _load_model(WhisperModel, self.config.model, device, compute_type)
        prompt = build_initial_prompt(hotwords, language, initial_prompt)

        if progress:
            progress("识别中…")
        audio_input = _audio_input(audio)
        options = {
            "language": language or None,
            "initial_prompt": prompt,
            "beam_size": max(1, self.config.beam_size),
            "vad_filter": self.config.vad,
            "vad_parameters": {"min_silence_duration_ms": 500} if self.config.vad else None,
            "condition_on_previous_text": False,  # 减少长视频里的复读机现象
            "word_timestamps": False,
        }
        if hotwords and _supports_option(model.transcribe, "hotwords"):
            # faster-whisper 1.2 的原生 hotwords 比只靠 initial_prompt 更能纠正产品名
            options["hotwords"] = " ".join(hotwords)
        batch_size = max(1, self.config.batch_size)
        if batch_size > 1:
            try:
                from faster_whisper import BatchedInferencePipeline

                runner = BatchedInferencePipeline(model)
                segments, info = runner.transcribe(
                    audio_input,
                    batch_size=batch_size,
                    without_timestamps=False,
                    **options,
                )
            except (ImportError, AttributeError, TypeError):
                if progress:
                    progress("当前 faster-whisper 不支持批处理，回退单路识别…")
                segments, info = model.transcribe(audio_input, **options)
        else:
            segments, info = model.transcribe(audio_input, **options)

        utterances: list[Utterance] = []
        total = getattr(info, "duration", 0) or 0
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            utterances.append(
                Utterance(
                    start=round(float(segment.start), 3),
                    end=round(float(segment.end), 3),
                    text=text,
                    confidence=_confidence(getattr(segment, "avg_logprob", None)),
                )
            )
            if progress and len(utterances) % 25 == 0:
                progress(f"识别中… {hms(segment.end)} / {hms(total)}")

        return utterances


def _audio_input(audio: Path):
    """直接把 16k/mono/s16 的 wav 读成 float32 数组，绕开 PyAV 解码。

    faster-whisper 1.2 用 `av.open(..., metadata_errors="ignore")` 解码，
    而 PyAV 19 删掉了这个参数，会直接抛 TypeError。我们自己的音轨格式是固定的，
    用标准库 wave 解码既稳定又快。
    """
    try:
        import wave

        import numpy as np
    except ImportError:
        return str(audio)

    try:
        with wave.open(str(audio), "rb") as handle:
            if (
                handle.getnchannels(),
                handle.getsampwidth(),
                handle.getframerate(),
            ) != (1, 2, 16000):
                return str(audio)
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, OSError):
        return str(audio)

    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _supports_option(fn, name: str) -> bool:
    """老版本 faster-whisper 没有 hotwords 参数，先探测再传，避免 TypeError。"""
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _load_model(model_cls, model_name: str, device: str, compute_type: str):
    try:
        return model_cls(model_name, device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if compute_type != "default" and _looks_like_compute_problem(message):
            # 某些 GPU/驱动不支持 float16，退回到最保守的配置
            try:
                return model_cls(model_name, device=device, compute_type="default")
            except Exception as retry_exc:  # noqa: BLE001
                message = str(retry_exc)
                exc = retry_exc
        raise StageError(
            f"加载模型失败：{model_name}（{device}/{compute_type}）",
            hint=_model_hint(message),
        ) from exc


def _looks_like_compute_problem(message: str) -> bool:
    lowered = message.lower()
    return any(
        keyword in lowered
        for keyword in (
            "compute type",
            "float16",
            "cuda",
            "cublas",
            "cudnn",
            "dtype",
            "not supported",
            "out of memory",
        )
    )


def _model_hint(message: str) -> str:
    lowered = message.lower()
    download_keys = ("xethub", "huggingface", "401", "connection", "offline", "cas ")
    if any(key in lowered for key in download_keys):
        return (
            "模型下载失败。依次尝试：\n"
            '  1) 关闭 xet：$env:HF_HUB_DISABLE_XET="1"\n'
            '  2) 国内换镜像：$env:HF_ENDPOINT="https://hf-mirror.com"\n'
            '  3) 改用云端：asr.provider = "openai"'
        )
    return (
        '试试 asr.device = "cpu" 或换小一点的 asr.model（small / medium）。'
        f"\n原始错误：{message[:300]}"
    )


def _pick_device(requested: str) -> str:
    if requested and requested != "auto":
        return requested
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _pick_compute(device: str, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    return "float16" if device == "cuda" else "int8"


def _confidence(avg_logprob: float | None) -> float | None:
    if avg_logprob is None:
        return None
    try:
        return round(min(1.0, max(0.0, math.exp(avg_logprob))), 3)
    except (ValueError, OverflowError):
        return None
