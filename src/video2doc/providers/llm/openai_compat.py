"""OpenAI 兼容的 /chat/completions。

一个实现覆盖 DeepSeek、通义、GLM、Kimi、Ollama、vLLM、LM Studio……
只要对方支持 `response_format: json_object` 或至少能按提示返回 JSON。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from ...errors import StageError
from .base import LLMProvider, ProgressFn, register

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@register
class OpenAICompatibleLLM(LLMProvider):
    name = "openai-compatible"
    label = "OpenAI 兼容对话接口"
    requires_module = "httpx"

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        import httpx

        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            raise StageError(
                f"环境变量 {self.config.api_key_env} 未设置",
                hint="设置后重试，或关闭 asr.correct_with_llm。",
            )

        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {api_key}"}

        last_error = ""
        for attempt in range(3):
            try:
                response = httpx.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout_sec,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                time.sleep(2**attempt)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                time.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                raise StageError(
                    f"LLM 接口返回 {response.status_code}：{response.text[:500]}",
                    hint=(
                        f"检查 base_url（{self.config.base_url}）、model（{self.config.model}）"
                        f"与 {self.config.api_key_env} 是否匹配。"
                    ),
                )

            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as exc:
                raise StageError(
                    f"LLM 响应格式不符合预期：{response.text[:300]}"
                ) from exc
            return _extract_json(content)

        raise StageError(f"LLM 请求失败：{last_error}")


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StageError(f"LLM 返回的不是合法 JSON：{content[:300]}") from exc
    if not isinstance(data, dict):
        raise StageError(f"LLM 返回的 JSON 不是对象：{content[:300]}")
    return data
