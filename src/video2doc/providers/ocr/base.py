"""OCR provider 接口与注册表。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from ...config import OCRConfig
from ...errors import StageError
from ...schema import TextBlock


class OCRProvider(ABC):
    name: str = ""
    label: str = ""
    requires_module: str | None = None
    # 引擎能否在多线程里共享同一个实例；不能就别并行
    parallel_safe: bool = False

    def __init__(self, config: OCRConfig) -> None:
        self.config = config

    def warmup(self) -> None:
        """并行前在主线程里加载模型，避免多个线程同时初始化。"""
        return None

    @abstractmethod
    def read(self, image: Path) -> list[TextBlock]:
        """返回按阅读顺序排好的文字块。"""


_REGISTRY: dict[str, type[OCRProvider]] = {}


def register(cls: type[OCRProvider]) -> type[OCRProvider]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} 缺少 name")
    _REGISTRY[cls.name] = cls
    return cls


def is_installed(module: str | None) -> bool:
    if not module:
        return True
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def resolve_name(name: str) -> str:
    """auto 的含义：装了就用，没装就优雅降级，不让整条流水线失败。"""
    if name == "auto":
        return "rapidocr" if is_installed("rapidocr_onnxruntime") else "none"
    return name


def create_ocr(config: OCRConfig) -> OCRProvider:
    name = resolve_name(config.provider)
    if name == "none":
        raise StageError("没有可用的 OCR 引擎", hint='pip install "video2doc[ocr]"')
    provider_cls = _REGISTRY.get(name)
    if provider_cls is None:
        raise StageError(
            f"未知的 ocr.provider：{name}",
            hint=f"可选：{', '.join(['auto', 'none', *_REGISTRY])}",
        )
    return provider_cls(config)


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    label: str
    ready: bool
    detail: str


def available_providers() -> list[ProviderStatus]:
    return [
        ProviderStatus(
            cls.name,
            cls.label,
            is_installed(cls.requires_module),
            "已安装" if is_installed(cls.requires_module) else f"缺少 {cls.requires_module}",
        )
        for cls in _REGISTRY.values()
    ]
