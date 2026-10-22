from .base import (
    OCRProvider,
    ProviderStatus,
    available_providers,
    create_ocr,
    is_installed,
    register,
    resolve_name,
)
from .rapidocr import RapidOCRProvider

__all__ = [
    "OCRProvider",
    "ProviderStatus",
    "RapidOCRProvider",
    "available_providers",
    "create_ocr",
    "is_installed",
    "register",
    "resolve_name",
]
