from __future__ import annotations

import httpx
import pytest

from video2doc.config import LLMConfig
from video2doc.errors import StageError
from video2doc.providers.llm.base import reset_usage, usage_snapshot
from video2doc.providers.llm.openai_compat import OpenAICompatibleLLM, _extract_json


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _payload(content: str, *, finish: str = "stop") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 123, "completion_tokens": 45},
    }


def test_complete_json_tracks_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: FakeResponse(_payload('{"summary": "ok"}'))
    )
    reset_usage()

    llm = OpenAICompatibleLLM(LLMConfig(api_key_env=""))

    assert llm.complete_json("system", "user") == {"summary": "ok"}
    usage = usage_snapshot()
    assert (usage.calls, usage.prompt_tokens, usage.completion_tokens) == (1, 123, 45)
    assert usage.total_tokens == 168


def test_truncated_output_reports_max_tokens_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(_payload('{"markdown": "写到一半', finish="length")),
    )
    reset_usage()

    llm = OpenAICompatibleLLM(LLMConfig(api_key_env="", max_tokens=1024))

    with pytest.raises(StageError) as info:
        llm.complete_json("system", "user")

    assert "max_tokens" in (info.value.hint or "")
    # 三次重试都花了 token，用量要照实记
    assert usage_snapshot().calls == 3


def test_extract_json_repairs_unescaped_backslashes() -> None:
    # 模型原样抄了 OCR 里的 Windows 路径，JSON 里成了非法转义
    content = '{"markdown": "打开 C:\\Users\\me\\AppData 再试"}'

    assert _extract_json(content) == {"markdown": "打开 C:\\Users\\me\\AppData 再试"}


def test_extract_json_tolerates_raw_newlines_in_strings() -> None:
    content = '{"markdown": "第一行\n第二行"}'

    assert _extract_json(content)["markdown"] == "第一行\n第二行"


def test_extract_json_keeps_code_fences_inside_markdown() -> None:
    content = '```json\n{"summary": "演示", "markdown": "```js\\nconst x = 1;\\n```\\n正文"}\n```'

    payload = _extract_json(content)

    assert "```js" in payload["markdown"]
    assert payload["markdown"].endswith("正文")


def test_extract_json_ignores_trailing_text() -> None:
    payload = _extract_json('{"summary": "ok", "markdown": "正文"}\n\n[]')

    assert payload == {"summary": "ok", "markdown": "正文"}


def test_extract_json_unrecoverable_error_mentions_position() -> None:
    with pytest.raises(StageError) as info:
        _extract_json('{"markdown": "没写结尾}')

    assert "@" in info.value.message
