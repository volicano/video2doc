"""ASR provider 接口与注册表。

新增一个服务只需要：写一个类 + 加一行 @register。
配置里改 asr.provider 即可切换，其余代码不用动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from ...config import ASRConfig
from ...errors import StageError
from ...schema import Utterance

ProgressFn = Callable[[str], None]


class ASRProvider(ABC):
    name: str = ""
    label: str = ""
    requires_module: str | None = None

    def __init__(self, config: ASRConfig) -> None:
        self.config = config

    @abstractmethod
    def transcribe(
        self,
        audio: Path,
        *,
        language: str,
        hotwords: list[str],
        initial_prompt: str | None = None,
        progress: ProgressFn | None = None,
    ) -> list[Utterance]:
        """返回带时间戳的句子。时间戳必须是模型真实输出的，不能编。"""


_REGISTRY: dict[str, type[ASRProvider]] = {}


def register(cls: type[ASRProvider]) -> type[ASRProvider]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} 缺少 name")
    _REGISTRY[cls.name] = cls
    return cls


def create_provider(name: str, config: ASRConfig) -> ASRProvider:
    provider_cls = _REGISTRY.get(name)
    if provider_cls is None:
        raise StageError(
            f"未知的 asr.provider：{name}",
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
