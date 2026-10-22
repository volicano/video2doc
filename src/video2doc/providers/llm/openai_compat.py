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
from .base import USAGE, LLMProvider, ProgressFn, register

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
# 反斜杠后面不是合法转义字符：多半是模型原样抄了 Windows 路径
_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])')
RETRY_HINT = (
    "\n\n注意：上一次输出没有形成完整 JSON（可能被长度限制截断）。"
    "请重新输出合法 JSON，并把正文压缩到 500 字以内：宁可少写，也不要被截断。"
)


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

        api_key = ""
        if self.config.api_key_env:
            api_key = os.environ.get(self.config.api_key_env, "").strip()
            if not api_key:
                raise StageError(
                    f"环境变量 {self.config.api_key_env} 未设置",
                    hint="设置后重试；或设 write.enabled = false，只跑转写和抽帧。",
                )

        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        user_prompt = user
        last_error = ""

        for attempt in range(3):
            payload = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "response_format": {"type": "json_object"},
            }
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
                data = response.json()
                message = data["choices"][0]
                content = message["message"]["content"]
                finish = message.get("finish_reason", "")
            except (KeyError, IndexError, ValueError) as exc:
                raise StageError(
                    f"LLM 响应格式不符合预期：{response.text[:300]}"
                ) from exc

            usage = data.get("usage") or {}
            USAGE.add(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

            try:
                return _extract_json(content)
            except StageError as exc:
                # 被 max_tokens 截断时重试，提示模型压缩篇幅
                truncated = finish == "length"
                if attempt >= 2:
                    if truncated:
                        raise StageError(
                            "LLM 输出被 max_tokens 截断，没能凑成完整 JSON",
                            hint=(
                                f"当前 llm.max_tokens = {self.config.max_tokens}：调大它，"
                                "或调小 write.max_section_chars 后再重跑。"
                            ),
                        ) from exc
                    raise
                user_prompt = user + RETRY_HINT
                last_error = f"JSON 解析失败（finish_reason={finish or '未知'}）"

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
        return _loads(text)
    except json.JSONDecodeError as exc:
        # 模型抄 OCR 里的 Windows 路径时经常忘了转义反斜杠
        repaired = _ESCAPE_RE.sub(r"\\\\", text)
        if repaired != text:
            try:
                return _loads(repaired)
            except json.JSONDecodeError:
                pass
        tail = content[-160:].replace("\n", " ")
        raise StageError(
            f"LLM 返回的不是合法 JSON（{exc.msg} @ {exc.pos}，末尾：…{tail}）"
        ) from exc


def _loads(text: str) -> dict[str, Any]:
    data = json.loads(text, strict=False)
    if not isinstance(data, dict):
        raise StageError(f"LLM 返回的 JSON 不是对象：{text[:300]}")
    return data
