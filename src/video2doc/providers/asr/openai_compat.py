"""OpenAI 兼容的 /audio/transcriptions。

用 HTTP 直连而不是装 openai SDK，好处是能对接任何兼容实现：
OpenAI、Azure、硅基流动、Ollama 旁边自建的 whisper 服务、vLLM 等。
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any

from ...errors import StageError
from ...hotwords import build_initial_prompt
from ...schema import Utterance
from .base import ASRProvider, ProgressFn, register


@register
class OpenAICompatibleProvider(ASRProvider):
    name = "openai"
    label = "OpenAI 兼容音频接口"
    requires_module = "httpx"

    def transcribe(
        self,
        audio: Path,
        *,
        language: str,
        hotwords: list[str],
        initial_prompt: str | None = None,
        progress: ProgressFn | None = None,
    ) -> list[Utterance]:
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            raise StageError(
                f"环境变量 {self.config.api_key_env} 未设置",
                hint=(
                    f'PowerShell: $env:{self.config.api_key_env}="sk-..."；'
                    f"bash: export {self.config.api_key_env}=sk-..."
                ),
            )

        endpoint = self.config.base_url.rstrip("/") + "/audio/transcriptions"
        data: dict[str, str] = {
            "model": self.config.model,
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if language:
            data["language"] = language
        prompt = build_initial_prompt(hotwords, language, initial_prompt)
        if prompt:
            data["prompt"] = prompt

        if progress:
            progress(f"上传音频（{audio.stat().st_size / 1024 / 1024:.1f} MB）…")

        payload = self._post(endpoint, api_key, data, audio)
        utterances = _parse_payload(payload)
        if not utterances:
            raise StageError(
                "接口返回成功，但没有识别出文本",
                hint="确认音轨不是静音；部分兼容服务要求 model 名正确（如 whisper-1）。",
            )
        return utterances

    def _post(
        self, endpoint: str, api_key: str, data: dict[str, str], audio: Path
    ) -> dict[str, Any]:
        import httpx

        headers = {"Authorization": f"Bearer {api_key}"}
        last_error = ""
        for attempt in range(3):
            try:
                with audio.open("rb") as handle:
                    files = {"file": (audio.name, handle, "audio/wav")}
                    response = httpx.post(
                        endpoint,
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=self.config.timeout_sec,
                    )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                time.sleep(2**attempt)
                continue

            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError:
                    return {"text": response.text}

            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2**attempt)
                continue

            raise StageError(
                f"ASR 接口返回 {response.status_code}：{response.text[:600]}",
                hint=(
                    f"检查 base_url（当前 {self.config.base_url}）"
                    f"与 model（当前 {self.config.model}）。"
                ),
            )

        raise StageError(f"ASR 接口请求失败：{last_error}")


def _parse_payload(payload: dict[str, Any]) -> list[Utterance]:
    utterances: list[Utterance] = []

    for segment in payload.get("segments") or []:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        utterances.append(
            Utterance(
                start=round(float(segment.get("start") or 0), 3),
                end=round(float(segment.get("end") or 0), 3),
                text=text,
                confidence=_confidence(segment.get("avg_logprob")),
            )
        )
    if utterances:
        return utterances

    words = payload.get("words") or []
    if words:
        return _group_words(words)

    text = (payload.get("text") or "").strip()
    if text:
        # 没有时间戳，下游对齐会退化，但至少不丢内容
        return [Utterance(start=0.0, end=0.0, text=text)]
    return []


def _group_words(
    words: list[dict[str, Any]], gap: float = 0.6, max_chars: int = 80
) -> list[Utterance]:
    utterances: list[Utterance] = []
    buffer: list[dict[str, Any]] = []

    def flush() -> None:
        if not buffer:
            return
        text = "".join(str(word.get("word") or "") for word in buffer).strip()
        if text:
            utterances.append(
                Utterance(
                    start=round(float(buffer[0].get("start") or 0), 3),
                    end=round(float(buffer[-1].get("end") or 0), 3),
                    text=text,
                )
            )
        buffer.clear()

    for word in words:
        if buffer:
            prev_end = float(buffer[-1].get("end") or 0)
            start = float(word.get("start") or 0)
            length = sum(len(str(w.get("word") or "")) for w in buffer)
            if start - prev_end > gap or length + len(str(word.get("word") or "")) > max_chars:
                flush()
        buffer.append(word)
    flush()
    return utterances


def _confidence(avg_logprob: float | None) -> float | None:
    if avg_logprob is None:
        return None
    try:
        return round(min(1.0, max(0.0, math.exp(float(avg_logprob)))), 3)
    except (TypeError, ValueError, OverflowError):
        return None
