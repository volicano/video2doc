"""LLM provider 接口与注册表。

只暴露一个 `complete_json`：我们需要的永远是结构化输出，
自由文本会让人忍不住去 parse 正则，最后在边界情况上翻车。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any

from ...config import LLMConfig
from ...errors import StageError
from ...schema import UsageStats

ProgressFn = Callable[[str], None]

# 进程内累计当前任务的 token 用量。CLI 一次只跑一个任务（batch 串行），
# 所以不需要加锁，pipeline 在每个任务开始时 reset 一次。
USAGE = UsageStats()


def reset_usage() -> None:
    USAGE.calls = 0
    USAGE.prompt_tokens = 0
    USAGE.completion_tokens = 0


def usage_snapshot() -> UsageStats:
    return USAGE.model_copy()


class LLMProvider(ABC):
    name: str = ""
    label: str = ""
    requires_module: str | None = None

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abstractmethod
    def complete_json(
        self,
        system: str,
        user: str,
        *,
        progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        """返回一个 JSON 对象。实现方负责重试与容错。"""


_REGISTRY: dict[str, type[LLMProvider]] = {}


def register(cls: type[LLMProvider]) -> type[LLMProvider]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} 缺少 name")
    _REGISTRY[cls.name] = cls
    return cls


def create_llm_from_config(config: LLMConfig) -> LLMProvider:
    provider_cls = _REGISTRY.get(config.provider)
    if provider_cls is None:
        raise StageError(
            f"未知的 llm.provider：{config.provider}",
            hint=f"可选：{', '.join(_REGISTRY)}",
        )
    return provider_cls(config)


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    label: str
    ready: bool
    detail: str


def available_providers() -> list[ProviderStatus]:
    statuses: list[ProviderStatus] = []
    for provider_cls in _REGISTRY.values():
        module = provider_cls.requires_module
        if not module:
            statuses.append(ProviderStatus(provider_cls.name, provider_cls.label, True, "内置"))
            continue
        try:
            ready = find_spec(module) is not None
        except (ImportError, ValueError):
            ready = False
        statuses.append(
            ProviderStatus(
                provider_cls.name,
                provider_cls.label,
                ready,
                "已安装" if ready else f"缺少 {module}",
            )
        )
    return statuses
