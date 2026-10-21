"""ASR provider 注册入口。

显式 import 每个实现，保证 @register 一定被执行。
"""

from .base import (
    ASRProvider,
    ProgressFn,
    ProviderStatus,
    available_providers,
    create_provider,
    register,
)
from .faster_whisper import FasterWhisperProvider
from .openai_compat import OpenAICompatibleProvider

__all__ = [
    "ASRProvider",
    "FasterWhisperProvider",
    "OpenAICompatibleProvider",
    "ProgressFn",
    "ProviderStatus",
    "available_providers",
    "create_provider",
    "register",
]
