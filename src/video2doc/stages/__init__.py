"""流水线阶段。"""

from .align import AlignStage
from .audio import AudioStage
from .correct import CorrectStage
from .frames import FramesStage
from .ingest import IngestStage
from .ocr import OCRStage
from .transcript import TranscriptStage


def default_stages() -> list:
    """顺序即执行顺序，也是缓存链的顺序。"""
    return [
        IngestStage(),
        AudioStage(),
        TranscriptStage(),
        CorrectStage(),
        FramesStage(),
        OCRStage(),
        AlignStage(),
    ]


__all__ = [
    "AlignStage",
    "AudioStage",
    "CorrectStage",
    "FramesStage",
    "IngestStage",
    "OCRStage",
    "TranscriptStage",
    "default_stages",
]
