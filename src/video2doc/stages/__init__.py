"""流水线阶段。"""

from .align import AlignStage
from .audio import AudioStage
from .correct import CorrectStage
from .critique import CritiqueStage
from .frames import FramesStage
from .ingest import IngestStage
from .ocr import OCRStage
from .render import RenderStage
from .segment import SegmentStage
from .transcript import TranscriptStage
from .write import WriteStage


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
        SegmentStage(),
        WriteStage(),
        CritiqueStage(),
        RenderStage(),
    ]


__all__ = [
    "AlignStage",
    "AudioStage",
    "CorrectStage",
    "CritiqueStage",
    "FramesStage",
    "IngestStage",
    "OCRStage",
    "RenderStage",
    "SegmentStage",
    "TranscriptStage",
    "WriteStage",
    "default_stages",
]
