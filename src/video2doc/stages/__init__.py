"""流水线阶段。"""

from .audio import AudioStage
from .correct import CorrectStage
from .ingest import IngestStage
from .transcript import TranscriptStage


def default_stages() -> list:
    """顺序即执行顺序，也是缓存链的顺序。"""
    return [IngestStage(), AudioStage(), TranscriptStage(), CorrectStage()]


__all__ = [
    "AudioStage",
    "CorrectStage",
    "IngestStage",
    "TranscriptStage",
    "default_stages",
]
